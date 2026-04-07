# Review Request: Assignment Bug in Multi-Tenant CRM

## Question

We have a multi-tenant CRM (Quart/SQLAlchemy backend, React/TypeScript frontend). The client reports that **assigning a Project to another user doesn't move it out of the assigner's view** — it stays in their own space instead of appearing only under the assignee. We suspect the same issue may affect Leads and Clients too.

Please investigate:
1. What are the most common causes of "assign to user" not filtering correctly?
2. Should the list query filter on `assigned_to`, `created_by`, or both — and what's the correct behavior for a CRM?
3. What's the best audit approach — backend query first or frontend filter first?
4. After reading the actual code, identify the specific bug(s) and recommend fixes.

## Context

### Backend Routes to Review
- @app/routes/projects.py — project list/update endpoints, pay attention to how `assigned_to` is used in queries
- @app/routes/leads.py — same for leads
- @app/routes/clients.py — same for clients
- @app/models.py — data model, check `assigned_to` field definitions

### Frontend Files to Review
- @/c/Users/William Boone/Desktop/Websites/pathsix-crm-custom/src/pages/Projects.tsx
- @/c/Users/William Boone/Desktop/Websites/pathsix-crm-custom/src/pages/ProjectDetailPage.tsx
- @/c/Users/William Boone/Desktop/Websites/pathsix-crm-custom/src/components/ui/ProjectForm.tsx
- @/c/Users/William Boone/Desktop/Websites/pathsix-crm-custom/src/components/ui/ProjectEditModal.tsx
- @/c/Users/William Boone/Desktop/Websites/pathsix-crm-custom/src/components/ui/ProjectsTable.tsx

### Key Questions to Answer
- In the backend list endpoint for projects: does the query filter by `assigned_to = current_user.id` OR `created_by = current_user.id` OR both? Which should it be?
- When a project is assigned to user B by user A, does user A's list still include it? Should it?
- Is the assignment being saved correctly to the DB, or is the PUT/PATCH endpoint ignoring the `assigned_to` field?
- Does the frontend re-fetch after assignment, or does it rely on stale local state?

### Recent Git Changes
Run `git log --oneline -10` and `git diff HEAD~5..HEAD -- app/routes/projects.py` to see recent changes to the projects route.

## Instructions
You are providing an independent review. Be critical and thorough.
- Read the referenced files to understand the full context
- Identify the specific line(s) causing the bug
- Distinguish between "assignment not saving" vs "assignment saving but list not filtering correctly"
- Check if the issue is consistent across projects, leads, and clients or isolated to one
- Suggest the minimal fix — don't over-engineer
- Be direct and opinionated
