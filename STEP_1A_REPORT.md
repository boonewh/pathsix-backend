# Step 1A Implementation Report: Lead Pydantic Schemas

## ✅ **Backend Implementation Complete**

### **Files Created:**
- `app/schemas/__init__.py` - Schema module initialization
- `app/schemas/leads.py` - Lead entity Pydantic schemas

### **Files Modified:**
- `app/routes/leads.py` - Added Pydantic validation to endpoints
- `requirements.txt` - Added pydantic and email-validator dependencies

### **Schemas Implemented:**

#### **1. LeadCreateSchema**
Used for `POST /api/leads`
- **Required**: `name` (1-100 chars)
- **Optional**: All contact fields, addresses, notes, type, lead_status
- **Validation**: Email format, phone cleanup, type constraints
- **Defaults**: phone_label="work", type="None", lead_status="open"

#### **2. LeadUpdateSchema** 
Used for `PUT /api/leads/{id}`
- **All fields optional** (partial updates)
- **Same validation rules** as create schema
- **Special handling**: Status changes, phone cleaning

#### **3. LeadAssignSchema**
Used for `PUT /api/leads/{id}/assign`
- **Required**: `assigned_to` (user ID)
- **Admin only** endpoint

### **Validation Features:**
- ✅ **Email validation** using EmailStr
- ✅ **String length limits** matching database constraints
- ✅ **Enum validation** for statuses and phone labels  
- ✅ **Phone number preprocessing** before database storage
- ✅ **Structured error responses** with field-level details

### **Error Response Format:**
```json
{
  "error": "Validation failed",
  "details": [
    {
      "loc": ["name"],
      "msg": "ensure this value has at least 1 characters",
      "type": "value_error.any_str.min_length"
    }
  ]
}
```

---

# Frontend Implementation Instructions

## **Step 1B: Create Matching Zod Schemas**

You need to create **identical validation rules** on the frontend. Here's exactly what to implement:

### **1. Install Dependencies**
```bash
npm install zod
```

### **2. Create Lead Schema File**
Create: `src/schemas/leadSchemas.ts`

```typescript
import { z } from 'zod'

// Phone label options (must match backend PHONE_LABELS)
export const phoneLabels = ['work', 'mobile', 'home', 'fax', 'other'] as const

// Lead status options (must match backend LEAD_STATUS_OPTIONS)  
export const leadStatuses = ['open', 'qualified', 'proposal', 'closed'] as const

// Type options (must match backend TYPE_OPTIONS)
export const typeOptions = [
  'None', 'Retail', 'Wholesale', 'Services', 'Manufacturing', 
  'Construction', 'Real Estate', 'Healthcare', 'Technology', 
  'Education', 'Finance & Insurance', 'Hospitality', 
  'Transportation & Logistics', 'Non-Profit', 'Government'
] as const

// Lead Create Schema (POST /api/leads)
export const leadCreateSchema = z.object({
  // Required fields
  name: z.string().min(1, "Company name is required").max(100),
  
  // Optional fields
  contact_person: z.string().max(100).optional().nullable(),
  contact_title: z.string().max(100).optional().nullable(),
  email: z.string().email().optional().nullable(),
  phone: z.string().max(20).optional().nullable(),
  phone_label: z.enum(phoneLabels).default('work'),
  secondary_phone: z.string().max(20).optional().nullable(),
  secondary_phone_label: z.enum(phoneLabels).optional().nullable(),
  address: z.string().max(255).optional().nullable(),
  city: z.string().max(100).optional().nullable(),
  state: z.string().max(100).optional().nullable(),
  zip: z.string().max(20).optional().nullable(),
  notes: z.string().optional().nullable(),
  type: z.enum(typeOptions).default('None'),
  lead_status: z.enum(leadStatuses).default('open')
})

// Lead Update Schema (PUT /api/leads/{id})
export const leadUpdateSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  contact_person: z.string().max(100).optional().nullable(),
  contact_title: z.string().max(100).optional().nullable(),
  email: z.string().email().optional().nullable(),
  phone: z.string().max(20).optional().nullable(),
  phone_label: z.enum(phoneLabels).optional(),
  secondary_phone: z.string().max(20).optional().nullable(),
  secondary_phone_label: z.enum(phoneLabels).optional().nullable(),
  address: z.string().max(255).optional().nullable(),
  city: z.string().max(100).optional().nullable(),
  state: z.string().max(100).optional().nullable(),
  zip: z.string().max(20).optional().nullable(),
  notes: z.string().optional().nullable(),
  type: z.enum(typeOptions).optional(),
  lead_status: z.enum(leadStatuses).optional()
})

// Lead Assign Schema (PUT /api/leads/{id}/assign)
export const leadAssignSchema = z.object({
  assigned_to: z.number().int().positive()
})

// Type exports for TypeScript
export type LeadCreateData = z.infer<typeof leadCreateSchema>
export type LeadUpdateData = z.infer<typeof leadUpdateSchema>
export type LeadAssignData = z.infer<typeof leadAssignSchema>
```

### **3. Update Your Forms**

#### **Lead Creation Form:**
```typescript
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { leadCreateSchema, LeadCreateData } from '../schemas/leadSchemas'

function CreateLeadForm() {
  const form = useForm<LeadCreateData>({
    resolver: zodResolver(leadCreateSchema),
    defaultValues: {
      phone_label: 'work',
      type: 'None',
      lead_status: 'open'
    }
  })
  
  // Form submission with validation
  const onSubmit = async (data: LeadCreateData) => {
    try {
      const response = await fetch('/api/leads', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
      
      if (!response.ok) {
        const error = await response.json()
        // Handle validation errors from backend
        console.error('Validation failed:', error.details)
        return
      }
      
      // Success handling
    } catch (error) {
      console.error('Submit failed:', error)
    }
  }
  
  // Your form JSX with form.register, form.formState.errors, etc.
}
```

#### **Lead Update Form:**
```typescript
import { leadUpdateSchema, LeadUpdateData } from '../schemas/leadSchemas'

function EditLeadForm({ leadData }) {
  const form = useForm<LeadUpdateData>({
    resolver: zodResolver(leadUpdateSchema),
    defaultValues: leadData
  })
  
  // Similar pattern for updates
}
```

### **4. Validation Rules Summary**

**✅ These must match exactly between frontend/backend:**

| Field | Required | Max Length | Validation |
|-------|----------|------------|------------|
| `name` | ✅ Yes | 100 chars | Non-empty string |
| `contact_person` | ❌ No | 100 chars | - |
| `contact_title` | ❌ No | 100 chars | - |
| `email` | ❌ No | - | Valid email format |
| `phone` | ❌ No | 20 chars | - |
| `phone_label` | ❌ No | - | Enum: work/mobile/home/fax/other |
| `address` | ❌ No | 255 chars | - |
| `city` | ❌ No | 100 chars | - |
| `state` | ❌ No | 100 chars | - |
| `zip` | ❌ No | 20 chars | - |
| `type` | ❌ No | - | Enum: None/Retail/Wholesale/etc. |
| `lead_status` | ❌ No | - | Enum: open/qualified/proposal/closed |

### **5. Next Steps**

1. **Test validation sync** - Try submitting invalid data and verify both frontend and backend catch the same errors
2. **Implement in your existing forms** - Replace current form validation with these Zod schemas  
3. **Test error handling** - Ensure backend validation errors display properly in your UI

---

## **Ready for Step 1C**

Once frontend validation is implemented, you can move to **Step 1C**: Adding runtime backend validation to remaining endpoints and **Step 1D**: Testing the full validation pipeline.

The validation foundation is now solid and following your improvement map rules! 🎯