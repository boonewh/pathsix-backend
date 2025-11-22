import sqlite3

def show_all_phone_data(conn):
    cursor = conn.cursor()
    
    print("\n=== CLIENTS ===")
    cursor.execute('SELECT id, name, phone, secondary_phone FROM clients')
    for row in cursor.fetchall():
        print(f"ID: {row[0]}, Name: {row[1]}, Phone: {row[2]}, Secondary: {row[3]}")
    
    print("\n=== LEADS ===")
    cursor.execute('SELECT id, name, phone, secondary_phone FROM leads')
    for row in cursor.fetchall():
        print(f"ID: {row[0]}, Name: {row[1]}, Phone: {row[2]}, Secondary: {row[3]}")
    
    print("\n=== CONTACTS ===")
    cursor.execute('SELECT id, first_name, last_name, phone, secondary_phone FROM contacts')
    for row in cursor.fetchall():
        print(f"ID: {row[0]}, Name: {row[1]} {row[2]}, Phone: {row[3]}, Secondary: {row[4]}")
    
    print("\n=== INTERACTIONS ===")
    cursor.execute('SELECT id, phone FROM interactions WHERE phone IS NOT NULL')
    for row in cursor.fetchall():
        print(f"ID: {row[0]}, Phone: {row[1]}")

def add_test_data_all_tables(conn):
    cursor = conn.cursor()
    
    # Add test leads with various phone formats
    test_leads = [
        ('Test Lead 1', '(913) 555-1111', '913-555-2222'),
        ('Test Lead 2', '913.555.3333', '(913) 555-4444'),
        ('Test Lead 3', '9135555555', None),
    ]
    
    for name, phone, secondary in test_leads:
        cursor.execute('''
            INSERT INTO leads (tenant_id, created_by, name, phone, secondary_phone, created_at, phone_label, secondary_phone_label, lead_status) 
            VALUES (1, 1, ?, ?, ?, datetime('now'), 'work', 'mobile', 'open')
        ''', (name, phone, secondary))
    
    # Add test contacts
    test_contacts = [
        ('John', 'Doe', '(785) 555-1111', '785-555-2222'),
        ('Jane', 'Smith', '785.555.3333', None),
    ]
    
    for first, last, phone, secondary in test_contacts:
        cursor.execute('''
            INSERT INTO contacts (tenant_id, first_name, last_name, phone, secondary_phone, created_at, phone_label, secondary_phone_label) 
            VALUES (1, ?, ?, ?, ?, datetime('now'), 'work', 'mobile')
        ''', (first, last, phone, secondary))
    
    conn.commit()
    print("Added test data to all tables!")

def migrate_table_phone_field(conn, table_name, field_name):
    cursor = conn.cursor()
    
    # Clean phone numbers (10 digits only, add +1 prefix)
    update_sql = f'''
        UPDATE {table_name} 
        SET {field_name} = '+1' || REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({field_name}, '(', ''), ')', ''), '-', ''), '.', ''), ' ', ''), '+1', '')
        WHERE {field_name} IS NOT NULL 
        AND LENGTH(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({field_name}, '(', ''), ')', ''), '-', ''), '.', ''), ' ', ''), '+1', '')) = 10
    '''
    
    cursor.execute(update_sql)
    affected_rows = cursor.rowcount
    print(f"  Cleaned {affected_rows} records in {table_name}.{field_name}")
    return affected_rows

def migrate_all_tables(conn):
    cursor = conn.cursor()
    
    # Define all tables and their phone fields
    tables_and_fields = [
        ('clients', ['phone', 'secondary_phone']),
        ('leads', ['phone', 'secondary_phone']),
        ('contacts', ['phone', 'secondary_phone']),
        ('interactions', ['phone'])
    ]
    
    total_cleaned = 0
    
    for table, phone_fields in tables_and_fields:
        print(f"\nMigrating table: {table}")
        for field in phone_fields:
            cleaned_count = migrate_table_phone_field(conn, table, field)
            total_cleaned += cleaned_count
    
    conn.commit()
    print(f"\n✅ Migration completed! Total records cleaned: {total_cleaned}")

def verify_migration(conn):
    """Check for any remaining unformatted phone numbers"""
    cursor = conn.cursor()
    
    print("\n=== VERIFICATION: Looking for unformatted phone numbers ===")
    
    # Check for phone numbers that don't start with +1 but look like US numbers
    verification_queries = [
        ("clients", "phone"),
        ("clients", "secondary_phone"),
        ("leads", "phone"),
        ("leads", "secondary_phone"),
        ("contacts", "phone"),
        ("contacts", "secondary_phone"),
        ("interactions", "phone")
    ]
    
    issues_found = False
    
    for table, field in verification_queries:
        cursor.execute(f'''
            SELECT id, {field} FROM {table} 
            WHERE {field} IS NOT NULL 
            AND {field} NOT LIKE '+1%'
            AND LENGTH(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({field}, '(', ''), ')', ''), '-', ''), '.', ''), ' ', '')) >= 7
        ''')
        
        unformatted = cursor.fetchall()
        if unformatted:
            issues_found = True
            print(f"⚠️  {table}.{field} has unformatted numbers:")
            for row in unformatted:
                print(f"   ID {row[0]}: {row[1]}")
    
    if not issues_found:
        print("✅ All phone numbers are properly formatted!")

# Main execution
if __name__ == "__main__":
    # Create backup first
    import shutil
    print("Creating backup: app.db.backup_before_full_migration")
    shutil.copy('app.db', 'app.db.backup_before_full_migration')
    
    conn = sqlite3.connect('app.db')
    
    print("=== BEFORE MIGRATION ===")
    show_all_phone_data(conn)
    
    print("\n=== ADDING TEST DATA TO ALL TABLES ===")
    add_test_data_all_tables(conn)
    show_all_phone_data(conn)
    
    print("\n=== RUNNING FULL MIGRATION ===")
    migrate_all_tables(conn)
    
    print("\n=== AFTER MIGRATION ===")
    show_all_phone_data(conn)
    
    print("\n=== VERIFICATION ===")
    verify_migration(conn)
    
    conn.close()
    print("\n🎉 Full phone migration completed!")