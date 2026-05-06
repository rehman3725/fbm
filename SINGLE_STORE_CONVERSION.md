# SINGLE-STORE CONVERSION PLAN - FBM Server App

## 🎯 OBJECTIVE
Convert multi-tenant FBM Server app to single-store standalone application

## 📋 CHANGES REQUIRED

### 1. Remove Tenancy Module
- Delete `tenancy.py` entirely
- Remove all tenancy-related imports from `main.py`

### 2. Update Models (`models.py`)
- Remove `TenantScopedMixin` class
- Remove `Tenant` model
- Remove `TenantFeature` model
- Remove `tenant_id` columns from all models
- Remove tenant-related unique constraints
- Update all model definitions to inherit from `db.Model` directly

### 3. Update Main App (`main.py`)
- Remove tenancy imports
- Remove `init_tenancy()` call
- Remove tenant-related middleware
- Update authentication to work without tenant context

### 4. Update Blueprints
- Remove tenant checks from all blueprints
- Remove tenant-based authorization
- Update queries to work without tenant filtering

### 5. Update Authentication
- Remove tenant-based user roles
- Simplify user permissions
- Remove tenant context from login/logout

### 6. Database Migration
- Create script to remove `tenant_id` columns
- Migrate existing data (keep all records)
- Update database schema

### 7. Update Templates & UI
- Remove tenant selection UI
- Update navigation to work without tenant context
- Remove tenant-related forms

## 🔄 IMPLEMENTATION ORDER

1. **Backup current database**
2. **Create database migration script**
3. **Update models.py** (remove tenant columns)
4. **Update main.py** (remove tenancy)
5. **Update blueprints** (remove tenant checks)
6. **Delete tenancy.py**
7. **Test application**
8. **Update documentation**

## ⚠️ CRITICAL CONSIDERATIONS

- **Data Preservation**: All existing data must be kept
- **Foreign Keys**: Remove tenant_id FKs but keep other relationships
- **Authentication**: Root user becomes regular admin
- **Unique Constraints**: Remove tenant-based uniqueness
- **Audit Trail**: Maintain audit logs without tenant context

## ✅ SUCCESS CRITERIA

- App starts without tenancy errors
- All data accessible without tenant selection
- Authentication works for single store
- No tenant-related UI elements
- Database schema simplified