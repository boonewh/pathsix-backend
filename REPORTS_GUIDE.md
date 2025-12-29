# PathSix CRM - Reports Guide

Complete reference for all 10 reporting endpoints in the PathSix CRM.

---

## Quick Reference

All endpoints require authentication and are at `/api/reports/*`

| Report | Endpoint | Purpose |
|--------|----------|---------|
| Sales Pipeline | `/pipeline` | Track leads by stage and project value |
| Lead Source | `/lead-source` | Which channels bring best leads |
| Conversion Rate | `/conversion-rate` | Funnel effectiveness and timing |
| Revenue by Client | `/revenue-by-client` | Top clients by project value |
| User Activity | `/user-activity` | Team engagement (admin only) |
| Follow-Ups | `/follow-ups` | Overdue tasks and inactive contacts |
| Client Retention | `/client-retention` | Retention rate and churn |
| Project Performance | `/project-performance` | Win rates and project metrics |
| Upcoming Tasks | `/upcoming-tasks` | Scheduled meetings and calls |
| Revenue Forecast | `/revenue-forecast` | Weighted pipeline prediction |

---

## 1. Sales Pipeline Report

**Endpoint:** `GET /api/reports/pipeline`

**Purpose:** Front-line health check - tracks leads and projects by stage

**Parameters:**
- `start_date` (optional): ISO date (e.g., "2024-01-01")
- `end_date` (optional): ISO date
- `user_id` (optional, admin only): Filter by user

**Response:**
```json
{
  "leads": [
    {"status": "open", "count": 45},
    {"status": "qualified", "count": 23},
    {"status": "converted", "count": 8}
  ],
  "projects": [
    {"status": "pending", "count": 15, "total_value": 125000.00},
    {"status": "won", "count": 8, "total_value": 87500.00}
  ]
}
```

**Use For:**
- Daily/weekly pipeline reviews
- Identifying sales bottlenecks
- Team meetings and forecasting

---

## 2. Lead Source Report

**Endpoint:** `GET /api/reports/lead-source`

**Purpose:** Shows which marketing channels bring the best leads

**Parameters:**
- `start_date` (optional)
- `end_date` (optional)

**Response:**
```json
{
  "sources": [
    {
      "source": "Website",
      "total_leads": 120,
      "converted": 15,
      "qualified": 45,
      "conversion_rate": 12.5
    },
    {
      "source": "Referral",
      "total_leads": 85,
      "converted": 22,
      "conversion_rate": 25.88
    }
  ]
}
```

**Use For:**
- Marketing ROI analysis
- Budget allocation decisions
- Identifying high-performing channels

**Note:** Requires `lead_source` field on leads.

---

## 3. Conversion Rate Report

**Endpoint:** `GET /api/reports/conversion-rate`

**Purpose:** Measures funnel effectiveness and sales performance

**Parameters:**
- `start_date` (optional)
- `end_date` (optional)

**Response:**
```json
{
  "overall": {
    "total_leads": 250,
    "converted_leads": 42,
    "conversion_rate": 16.8,
    "avg_days_to_convert": 18.5
  },
  "by_user": [
    {
      "user_id": 5,
      "user_email": "john@example.com",
      "total_leads": 80,
      "converted": 18,
      "conversion_rate": 22.5
    }
  ]
}
```

**Use For:**
- Setting realistic sales goals
- Identifying top performers
- Team performance comparison
- Sales process optimization

---

## 4. Revenue by Client Report

**Endpoint:** `GET /api/reports/revenue-by-client`

**Purpose:** Shows high-value clients by total project value

**Parameters:**
- `start_date` (optional)
- `end_date` (optional)
- `limit` (optional, default: 50)

**Response:**
```json
{
  "clients": [
    {
      "client_id": 12,
      "client_name": "Acme Corp",
      "project_count": 8,
      "won_value": 450000.00,
      "pending_value": 125000.00,
      "total_value": 575000.00
    }
  ]
}
```

**Use For:**
- Account management prioritization
- Upselling opportunities
- Client relationship management
- Resource allocation

---

## 5. User Activity Report

**Endpoint:** `GET /api/reports/user-activity`

**Purpose:** Tracks team member engagement (admin only)

**Parameters:**
- `start_date` (optional)
- `end_date` (optional)

**Response:**
```json
{
  "users": [
    {
      "user_id": 5,
      "email": "john@example.com",
      "interactions": 142,
      "leads_assigned": 35,
      "clients_assigned": 18,
      "activity_count": 287
    }
  ]
}
```

**Use For:**
- CRM adoption monitoring
- Workload distribution
- Performance reviews
- Identifying inactive users

**Admin Only:** This endpoint requires admin role.

---

## 6. Follow-Up / Inactivity Report

**Endpoint:** `GET /api/reports/follow-ups`

**Purpose:** Highlights overdue tasks and inactive contacts

**Parameters:**
- `inactive_days` (optional, default: 30)

**Response:**
```json
{
  "overdue_follow_ups": [
    {
      "interaction_id": 234,
      "client_id": 15,
      "entity_name": "Acme Corp",
      "follow_up_date": "2024-11-15T14:00:00Z",
      "summary": "Follow up on proposal",
      "days_overdue": 5
    }
  ],
  "inactive_clients": [
    {
      "client_id": 22,
      "name": "Beta Industries",
      "last_interaction": "2024-10-01T10:00:00Z",
      "days_inactive": 45
    }
  ],
  "inactive_leads": [...]
}
```

**Use For:**
- Daily task management
- Preventing leads from going cold
- Proactive relationship management
- Reducing churn risk

**Pro Tip:** Check this report every morning.

---

## 7. Client Retention Report

**Endpoint:** `GET /api/reports/client-retention`

**Purpose:** Shows retention rate and churn patterns

**Parameters:**
- `start_date` (optional)
- `end_date` (optional)

**Response:**
```json
{
  "status_breakdown": [
    {"status": "new", "count": 25},
    {"status": "active", "count": 142},
    {"status": "inactive", "count": 18}
  ],
  "total_active": 167,
  "churned": 8,
  "active_with_recent_interactions": 98,
  "retention_rate": 95.43
}
```

**Use For:**
- Customer success measurement
- Churn pattern identification
- Board meetings and investor updates
- Retention improvement goals

**Benchmark:** Most B2B service businesses aim for 85%+ retention.

---

## 8. Project Performance Report

**Endpoint:** `GET /api/reports/project-performance`

**Purpose:** Summarizes project outcomes and success rates

**Parameters:**
- `start_date` (optional)
- `end_date` (optional)

**Response:**
```json
{
  "status_breakdown": [
    {"status": "pending", "count": 15, "total_value": 225000.00},
    {"status": "won", "count": 28, "total_value": 510000.00},
    {"status": "lost", "count": 7, "total_value": 85000.00}
  ],
  "total_projects": 50,
  "win_rate": 56.00,
  "avg_duration_days": 45.3,
  "avg_project_value": 16400.00
}
```

**Use For:**
- Sales team effectiveness
- Forecasting and goal setting
- Pricing optimization
- Process improvement

**Pro Tip:** A 50-60% win rate is typical for well-qualified opportunities.

---

## 9. Upcoming Tasks Report

**Endpoint:** `GET /api/reports/upcoming-tasks`

**Purpose:** Lists scheduled meetings, calls, and follow-ups

**Parameters:**
- `days` (optional, default: 30)
- `user_id` (optional, admin only)

**Response:**
```json
{
  "upcoming_tasks": [
    {
      "interaction_id": 456,
      "client_id": 12,
      "entity_name": "Acme Corp",
      "follow_up_date": "2024-11-25T10:00:00Z",
      "summary": "Product demo call",
      "status": "pending",
      "days_until": 3,
      "assigned_to": "john@example.com"
    }
  ]
}
```

**Use For:**
- Daily and weekly planning
- Team coordination
- Workload visibility
- Ensuring nothing falls through the cracks

---

## 10. Revenue Forecast Report

**Endpoint:** `GET /api/reports/revenue-forecast`

**Purpose:** Predicts future revenue with weighted pipeline

**Parameters:** None

**Response:**
```json
{
  "projects": [
    {
      "status": "pending",
      "count": 15,
      "total_value": 225000.00,
      "weighted_value": 67500.00,
      "weight": 0.3
    },
    {
      "status": "won",
      "count": 28,
      "total_value": 510000.00,
      "weighted_value": 510000.00,
      "weight": 1.0
    }
  ],
  "total_weighted_forecast": 577500.00,
  "lead_pipeline": [
    {"status": "open", "count": 45}
  ]
}
```

**Weighting Logic:**
- Pending: 30% (3 out of 10 typically close)
- Won: 100% (already closed)
- Lost: 0% (not happening)

**Use For:**
- Monthly/quarterly revenue planning
- Board presentations
- Cash flow projections
- Resource planning

**Pro Tip:** Compare forecast to actual results quarterly and adjust weights to match your business.

---

## Common Usage Patterns

### Date Range Filtering

```javascript
const params = new URLSearchParams();
if (startDate) params.append('start_date', startDate.toISOString().split('T')[0]);
if (endDate) params.append('end_date', endDate.toISOString().split('T')[0]);

const response = await fetch(`/api/reports/pipeline?${params}`, {
  headers: { 'Authorization': `Bearer ${token}` }
});
```

### Recommended Review Schedule

- **Daily:** Follow-Ups, Upcoming Tasks
- **Weekly:** Sales Pipeline, User Activity (managers)
- **Monthly:** All revenue and performance reports
- **Quarterly:** Retention, Conversion Rate, Lead Source (strategic)

### Authentication

All endpoints require JWT authentication:

```javascript
const headers = {
  'Authorization': `Bearer ${yourJWTToken}`,
  'Content-Type': 'application/json'
};
```

---

## Admin vs User Access

**Admin Only:**
- User Activity Report

**Admin Features:**
- Sales Pipeline: Can filter by `user_id`
- Conversion Rate: Sees `by_user` breakdown
- Upcoming Tasks: Can filter by `user_id`

**All Users:**
- All other endpoints (filtered to their assigned entities)

---

## Error Handling

Standard error format:

```json
{
  "error": "Error message here"
}
```

**HTTP Status Codes:**
- `200`: Success
- `400`: Bad request (invalid parameters)
- `401`: Unauthorized (missing/invalid token)
- `403`: Forbidden (admin-only endpoint)
- `500`: Server error

---

## Data Quality Tips

Reports are only as good as your data:

- Keep lead sources up to date
- Log all client interactions
- Update project statuses promptly
- Schedule follow-ups consistently
- Assign ownership clearly

---

## Chart Recommendations

**Suggested Libraries:** Recharts or Chart.js

**Report → Chart Type:**
- Sales Pipeline → Funnel chart
- Lead Source → Bar chart
- Conversion Rate → Gauge/percentage
- Revenue by Client → Sorted table
- User Activity → Bar chart comparison
- Follow-Ups → Alert list
- Client Retention → Donut chart
- Project Performance → Win rate gauge
- Upcoming Tasks → Calendar/timeline
- Revenue Forecast → Stacked bar with forecast line

---

## Files Reference

- `app/routes/reports.py` - All report endpoints
- `app/models.py` - Database models
- `app/constants.py` - Valid lead sources and statuses

For detailed user guide with business context, see the original `REPORTS_USER_GUIDE.md` if needed.
