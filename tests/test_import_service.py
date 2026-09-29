"""Import boundaries, partial failures and caller-owned durability."""
import asyncio
import io
import json
import time

import pytest
from authlib.jose import jwt
from openpyxl import Workbook
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from werkzeug.datastructures import FileStorage

from test_security_boundaries import crm
from app.models import ActivityLog, Lead, Tenant, User
from app.routes import imports
from app.services.imports import ImportService
from app.services.leads import LeadService
from app.services.principal import Principal
from app.utils import auth_utils

ADMIN = Principal(1, 1, frozenset({'admin'}))
MAPPINGS = [{'csvColumn': 'Company', 'leadField': 'name'},
            {'csvColumn': 'Email', 'leadField': 'email'}]


def upload(data='Company,Email\nImported One,one@example.com\n', filename='leads.csv'):
    return FileStorage(stream=io.BytesIO(data.encode() if isinstance(data, str) else data), filename=filename)


def http(app, path='/submit', *, data=None, mappings=MAPPINGS, assignee='a@example.test', user=1):
    token = jwt.encode({'alg': 'HS256'}, {'sub': user, 'roles': ['admin'], 'exp': int(time.time())+300},
                       app.config['SECRET_KEY']).decode()
    async def run():
        response = await app.test_client().post('/api/import/leads'+path,
            files={'file': upload() if data is None else data},
            form={'column_mappings': json.dumps(mappings), 'assigned_user_email': assignee},
            headers={'Authorization': 'Bearer '+token})
        return response.status_code, await response.get_json()
    return asyncio.run(run())


@pytest.mark.parametrize('restricted', [False, True])
def test_partial_import_is_tenant_bound_and_uses_shared_lead_rules(crm, restricted):
    _, factory, _ = crm
    with factory() as db:
        db.get(Tenant, 1).config = {'leads': {'statuses': ['Prospecting']}, 'businessTypes': ['Technology']}
        db.get(Tenant, 2).config = {'leads': {'statuses': ['FOREIGN']}}
        db.commit()
    runtime = auth_utils.SessionLocal if restricted else factory
    with runtime() as db:
        service = ImportService(db, ADMIN)
        result = service.submit(upload('Company,Email\nImported One,ONE@EXAMPLE.COM\n,missing@example.com\nImported Two,two@example.com\n'), MAPPINGS, 'a@example.test')
        assert result.response['successful_imports'] == 2
        assert result.response['failed_imports'] == 1
        assert result.response['failures'][0]['row'] == 3
        assert result.notification['recipient'] == 'a@example.test'
        assert '2 new leads' in result.notification['body'] and 'FOREIGN' not in result.notification['body']
        db.commit()
    with factory() as db:
        rows = db.query(Lead).filter(Lead.name.like('Imported%')).all()
        assert len(rows) == 2
        assert all((r.tenant_id, r.created_by, r.assigned_to, r.lead_status)==(1,1,1,'Prospecting') for r in rows)
        assert rows[0].email == 'one@example.com'
        events = db.query(ActivityLog).filter(ActivityLog.entity_id.in_([r.id for r in rows]), ActivityLog.entity_type=='lead').all()
        assert len(events) == 2 and all(e.tenant_id==1 and e.user_id==1 for e in events)
        assert db.get(Lead, 2).name == 'Private lead 2'


@pytest.mark.parametrize('restricted', [False, True])
def test_assignment_and_admin_denials_are_side_effect_free(crm, restricted):
    call, factory, app = crm
    runtime = auth_utils.SessionLocal if restricted else factory
    with factory() as db:
        db.get(User, 3).is_active = False
        db.commit()
    for email in ('b@example.test', 'ordinary@example.test', 'missing@example.test'):
        with runtime() as db:
            with pytest.raises(ValueError, match='Assigned user not found or inactive'):
                ImportService(db, ADMIN).submit(upload(), MAPPINGS, email)
        assert http(app, assignee=email)[0] == 400
    with runtime() as db:
        with pytest.raises(TypeError):
            ImportService(db, None)
        with pytest.raises(PermissionError):
            ImportService(db, Principal(3,1,frozenset()))
    with factory() as db:
        db.get(User, 1).roles = []
        db.commit()
    assert http(app)[0] == 403
    assert http(app, '/preview')[0] == 403
    assert call('GET','/api/import/leads/template')[0] == 403
    with factory() as db:
        assert db.query(Lead).count() == 2 and db.query(ActivityLog).count() == 0


@pytest.mark.parametrize('mapping', [None, {}, [None], [{}], [{'csvColumn':1,'leadField':'name'}],
    [{'csvColumn':'Company','leadField':'tenant_id'}], [{'csvColumn':'Company','leadField':'assigned_to'}],
    [{'csvColumn':'Missing','leadField':'name'}], [{'csvColumn':'Company','leadField':''}],
    MAPPINGS+[{'csvColumn':'Email','leadField':'name'}]])
def test_malformed_mappings_rejected_before_writes(crm, mapping):
    _, factory, app = crm
    with factory() as db:
        with pytest.raises(ValueError):
            ImportService(db, ADMIN).submit(upload(), mapping, 'a@example.test')
    assert http(app,mappings=mapping)[0] == 400
    with factory() as db:
        assert db.query(Lead).count() == 2 and db.query(ActivityLog).count() == 0


@pytest.mark.parametrize('restricted', [False, True])
def test_whole_import_and_activity_can_be_rolled_back(crm, restricted):
    _, factory, _ = crm
    with (auth_utils.SessionLocal if restricted else factory)() as db:
        result = ImportService(db, ADMIN).submit(upload(), MAPPINGS, 'a@example.test')
        assert result.response['successful_imports'] == 1
        db.rollback()
    with factory() as db:
        assert db.query(Lead).count() == 2 and db.query(ActivityLog).count() == 0


def test_row_constraint_failure_rolls_back_only_its_own_lead_and_audit(crm, monkeypatch):
    _, factory, _ = crm
    original = LeadService.create
    def insert_then_break(service, data, **kwargs):
        ident = original(service, data, **kwargs)
        if data.name == 'Bad':
            service.session.get(Lead, ident).name = None
            service.session.flush()
        return ident
    monkeypatch.setattr(LeadService, 'create', insert_then_break)
    with auth_utils.SessionLocal() as db:
        result = ImportService(db, ADMIN).submit(upload('Company,Email\nGood,\nBad,\nLast,\n'), MAPPINGS, 'a@example.test')
        assert result.response['successful_imports']==2 and result.response['failed_imports']==1
        assert result.response['failures'][0]['error']=='Row violates a data constraint'
        assert result.notification['body'].count('- ')==2
        db.commit()
    with factory() as db:
        assert db.query(Lead).count()==4 and db.query(ActivityLog).count()==2
        assert not db.query(Lead).filter(Lead.name=='Bad').count()


def test_database_outage_aborts_batch_and_never_sends_email(crm, monkeypatch):
    _, factory, app = crm
    delivered = []
    async def send(**kwargs): delivered.append(kwargs)
    monkeypatch.setattr(imports,'send_email',send)
    original = LeadService.create
    def break_second(service, data, **kwargs):
        if data.name=='Second':
            raise OperationalError('PRIVATE SQL', {'secret':'PRIVATE'}, Exception('PRIVATE'))
        return original(service,data,**kwargs)
    monkeypatch.setattr(LeadService,'create',break_second)
    status, body = http(app,data=upload('Company,Email\nFirst,\nSecond,\n'))
    assert status==500 and 'PRIVATE' not in json.dumps(body)
    assert not delivered
    with factory() as db:
        assert db.query(Lead).count()==2 and db.query(ActivityLog).count()==0


def test_email_only_after_commit_and_email_failure_does_not_undo_import(crm, monkeypatch):
    _, factory, app = crm
    messages=[]
    async def send(**kwargs):
        with factory() as db:
            assert db.query(Lead).filter(Lead.name=='Imported One').count()==1
        messages.append(kwargs)
        raise RuntimeError('SECRET email provider detail')
    monkeypatch.setattr(imports,'send_email',send)
    status, body=http(app)
    assert status==200 and body['successful_imports']==1 and len(messages)==1
    assert messages[0]['recipient']=='a@example.test'
    assert 'SECRET' not in json.dumps(body)


def test_failed_commit_and_all_invalid_rows_never_notify(crm, monkeypatch):
    _, factory, app = crm
    messages=[]
    async def send(**kwargs): messages.append(kwargs)
    monkeypatch.setattr(imports,'send_email',send)
    assert http(app,data=upload('Company,Email\n,invalid\n'))[1]['successful_imports']==0
    def no_commit(session):
        if not session.in_nested_transaction():
            raise OperationalError('SECRET',None,Exception('SECRET'))
    runtime=auth_utils.SessionLocal
    event.listen(runtime,'before_commit',no_commit)
    try:
        status,body=http(app)
    finally:
        event.remove(runtime,'before_commit',no_commit)
    assert status==500 and 'SECRET' not in json.dumps(body) and not messages
    with factory() as db:
        assert db.query(Lead).count()==2 and db.query(ActivityLog).count()==0


def test_csv_text_encoding_preview_and_normalization(crm):
    _, factory, app=crm
    data='Company,Zip,Phone,Phone Label,Secondary Phone,Email,Type\nCafé,00123,invalid,WORK,4325551234,A@EXAMPLE.COM,technology\n'.encode('cp1252')
    mapping=[{'csvColumn':c,'leadField':f} for c,f in zip(
        ['Company','Zip','Phone','Phone Label','Secondary Phone','Email','Type'],
        ['name','zip','phone','phone_label','secondary_phone','email','type'])]
    with factory() as db:
        db.get(Tenant,1).config={'businessTypes':['Technology']}
        db.commit()
    with auth_utils.SessionLocal() as db:
        service=ImportService(db,ADMIN)
        preview=service.preview(upload(data))
        assert preview['rows'][0][1]=='00123' and preview['rows'][0][0]=='Café'
        result=service.submit(upload(data),mapping,'a@example.test')
        assert result.response['warnings']==['Invalid phone on row 2']
        db.commit()
    with factory() as db:
        lead=db.query(Lead).filter(Lead.name=='Café').one()
        assert (lead.zip,lead.phone,lead.phone_label,lead.secondary_phone_label,lead.email,lead.type)==('00123',None,'work','mobile','a@example.com','Technology')
    assert http(app,'/preview',data=upload(data))[1]==preview


def test_xlsx_preview_and_submit(crm):
    _, factory, _=crm
    book=Workbook();sheet=book.active
    sheet.append(['Company','Email']);sheet.append(['Sheet lead','sheet@example.com'])
    stream=io.BytesIO();book.save(stream);book.close()
    with auth_utils.SessionLocal() as db:
        service=ImportService(db,ADMIN)
        assert service.preview(upload(stream.getvalue(),'leads.xlsx'))['totalRows']==1
        assert service.submit(upload(stream.getvalue(),'leads.xlsx'),MAPPINGS,'a@example.test').response['successful_imports']==1
        db.commit()
    with factory() as db:
        assert db.query(Lead).filter(Lead.name=='Sheet lead').one().tenant_id==1


def test_parser_bounds_duplicate_headers_and_bad_formats(crm, monkeypatch):
    _, factory, _=crm
    with factory() as db:
        service=ImportService(db,ADMIN,max_rows=1,max_bytes=100)
        for item in (None,upload('x','file.exe'),upload('x','file.xlsx'),upload(''),upload(' Company,Company\na,b'),
                     upload('Company,Email\na,b,c'),upload('Company\none\ntwo\n'),upload('x'*101)):
            with pytest.raises(ValueError): service.preview(item)
        assert service.preview(upload('Company,Email\nNA,\n'))['rows']==[['NA','']]
        assert 'Company Name' in service.template()
    book=Workbook();book.active.append(['Company']);stream=io.BytesIO();book.save(stream);book.close()
    monkeypatch.setattr('app.services.imports.MAX_EXPANDED_BYTES',1)
    with factory() as db:
        with pytest.raises(ValueError,match='Expanded spreadsheet'):
            ImportService(db,ADMIN).preview(upload(stream.getvalue(),'leads.xlsx'))


def test_preview_and_email_sample_are_bounded_and_template_is_unchanged(crm):
    call, factory, _=crm
    source='Company,Email\n'+''.join(f'Imported {i},\n' for i in range(12))
    with factory() as db:
        service=ImportService(db,ADMIN)
        preview=service.preview(upload(source))
        assert preview['totalRows']==12 and len(preview['rows'])==10
        assert db.query(Lead).count()==2 and db.query(ActivityLog).count()==0
        result=service.submit(upload(source),MAPPINGS,'a@example.test')
        assert result.notification['body'].count('- Imported ')==10
        assert '...and 2 more.' in result.notification['body']
        db.rollback()
        template=service.template()
    assert call('GET','/api/import/leads/template')==(200,template)


def test_invalid_json_mapping_is_a_safe_http_validation_error(crm):
    _, factory, app=crm
    token=jwt.encode({'alg':'HS256'},{'sub':1,'exp':int(time.time())+300},app.config['SECRET_KEY']).decode()
    async def run():
        response=await app.test_client().post('/api/import/leads/submit', files={'file':upload()},
            form={'column_mappings':'{broken','assigned_user_email':'a@example.test'},
            headers={'Authorization':'Bearer '+token})
        assert response.status_code==400
        assert (await response.get_json())['error']=='Invalid column mappings'
    asyncio.run(run())
    with factory() as db:
        assert db.query(Lead).count()==2
