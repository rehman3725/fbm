"""
Accounts module for financial management.
Provides comprehensive finance management including payments, receipts, expenditures, and account transfers.
"""

import logging
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, g
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func, or_, and_
from sqlalchemy.exc import IntegrityError
from models import db, Account, AccountCategory, AccountTransaction, Payment, SupplierPayment, FbmCashDrawerEntry, DirectSale, GRN, GRNItem, Supplier, Client, Booking, PendingBill
from tenancy import audit_log

# Module configuration
MODULE_CONFIG = {
    'name': 'Accounts Module',
    'description': 'Financial management and accounting',
    'url_prefix': '/accounts',
    'enabled': True,
    'requires_login': True,
    'allowed_roles': ['admin', 'user']
}

accounts_bp = Blueprint('accounts', __name__)
PK_TZ = ZoneInfo('Asia/Karachi')
logger = logging.getLogger(__name__)


@accounts_bp.before_request
def _accounts_permission_check():
    if not current_user.is_authenticated:
        return
    if current_user.role != 'admin' and not getattr(current_user, 'can_manage_payments', False):
        from flask import abort
        abort(403)


def pk_now():
    return datetime.now(PK_TZ).replace(tzinfo=None)


def pk_today():
    return pk_now().date()


def _resolve_client(client_input):
    value = (client_input or '').strip()
    if not value:
        return None
    client = Client.query.filter(func.lower(func.trim(Client.code)) == value.lower()).first()
    if client:
        return client
    return Client.query.filter(func.lower(func.trim(Client.name)) == value.lower()).first()


def _resolve_supplier(supplier_input):
    value = (supplier_input or '').strip()
    if not value:
        return None
    try:
        supplier_id = int(value)
    except Exception:
        supplier_id = None
    if supplier_id:
        supplier = Supplier.query.get(supplier_id)
        if supplier:
            return supplier
    return Supplier.query.filter(
        Supplier.is_active == True,
        func.lower(func.trim(Supplier.name)) == value.lower()
    ).first()


def _client_due_summary():
    summary = []
    clients = Client.query.filter_by(is_active=True).order_by(Client.name.asc()).all()
    for client in clients:
        client_name_norm = (client.name or '').strip().lower()
        if not client_name_norm:
            continue

        booking_debit = db.session.query(func.sum(Booking.amount)).filter(
            func.lower(func.trim(Booking.client_name)) == client_name_norm,
            Booking.is_void == False
        ).scalar() or 0
        booking_credit = db.session.query(func.sum(Booking.paid_amount)).filter(
            func.lower(func.trim(Booking.client_name)) == client_name_norm,
            Booking.is_void == False
        ).scalar() or 0
        payment_credit = db.session.query(func.sum(Payment.amount)).filter(
            func.lower(func.trim(Payment.client_name)) == client_name_norm,
            Payment.is_void == False,
            Payment.amount >= 0
        ).scalar() or 0
        payment_debit = db.session.query(func.sum(-Payment.amount)).filter(
            func.lower(func.trim(Payment.client_name)) == client_name_norm,
            Payment.is_void == False,
            Payment.amount < 0
        ).scalar() or 0
        sale_debit = db.session.query(func.sum(DirectSale.amount)).filter(
            func.lower(func.trim(DirectSale.client_name)) == client_name_norm,
            DirectSale.is_void == False
        ).scalar() or 0
        sale_credit = db.session.query(func.sum(DirectSale.paid_amount)).filter(
            func.lower(func.trim(DirectSale.client_name)) == client_name_norm,
            DirectSale.is_void == False
        ).scalar() or 0

        booking_discount = db.session.query(func.sum(Booking.discount)).filter(
            func.lower(func.trim(Booking.client_name)) == client_name_norm,
            Booking.is_void == False
        ).scalar() or 0
        sale_discount = db.session.query(func.sum(DirectSale.discount)).filter(
            func.lower(func.trim(DirectSale.client_name)) == client_name_norm,
            DirectSale.is_void == False
        ).scalar() or 0
        payment_discount = db.session.query(func.sum(Payment.discount)).filter(
            func.lower(func.trim(Payment.client_name)) == client_name_norm,
            Payment.is_void == False
        ).scalar() or 0

        opening_balance = float(getattr(client, 'opening_balance', 0) or 0)
        opening_debit = opening_balance if opening_balance > 0 else 0
        opening_credit = abs(opening_balance) if opening_balance < 0 else 0

        debit_total = opening_debit + float(booking_debit or 0) + float(sale_debit or 0) + float(payment_debit or 0)
        credit_total = (
            opening_credit
            + float(booking_credit or 0)
            + float(payment_credit or 0)
            + float(sale_credit or 0)
            + float(booking_discount or 0)
            + float(sale_discount or 0)
            + float(payment_discount or 0)
        )
        due_amount = debit_total - credit_total
        if due_amount <= 0:
            continue

        summary.append({
            'client_code': client.code,
            'client_name': client.name,
            'due_amount': due_amount
        })

    summary.sort(key=lambda x: (-x['due_amount'], x['client_name'].lower()))
    return summary


def _supplier_payable_summary():
    summary = []
    suppliers = Supplier.query.filter_by(is_active=True).order_by(Supplier.name.asc()).all()
    for supplier in suppliers:
        supplier_id = supplier.id

        # Calculate total GRN amounts for this supplier
        grn_totals = db.session.query(
            func.sum(GRN.loading_cost + GRN.freight_cost + GRN.other_expense + GRN.tax_amount - GRN.discount - GRN.adjustment_amount).label('costs'),
            func.sum(GRN.paid_amount).label('paid')
        ).filter(
            GRN.supplier_id == supplier_id,
            GRN.is_void == False
        ).first()

        # Calculate GRN items total
        grn_items_total = db.session.query(func.sum(GRNItem.qty * GRNItem.price_at_time)).filter(
            GRNItem.is_void == False,
            GRNItem.grn_id.in_(
                db.session.query(GRN.id).filter(
                    GRN.supplier_id == supplier_id,
                    GRN.is_void == False
                )
            )
        ).scalar() or 0

        grn_costs = float(grn_totals.costs or 0) if grn_totals else 0
        grn_paid = float(grn_totals.paid or 0) if grn_totals else 0
        total_grn_amount = grn_items_total + grn_costs

        # Supplier payments
        supplier_payments_total = db.session.query(func.sum(SupplierPayment.amount)).filter(
            SupplierPayment.supplier_id == supplier_id,
            SupplierPayment.is_void == False
        ).scalar() or 0

        # Opening balance (if supplier owes us, it's negative)
        opening_balance = float(getattr(supplier, 'opening_balance', 0) or 0)

        # Payable amount = what we owe to supplier
        # Positive means we owe money to supplier
        payable_amount = total_grn_amount - grn_paid - supplier_payments_total + opening_balance

        if payable_amount <= 0:
            continue

        summary.append({
            'supplier_id': supplier.id,
            'supplier_name': supplier.name,
            'payable_amount': payable_amount
        })

    summary.sort(key=lambda x: (-x['payable_amount'], x['supplier_name'].lower()))
    return summary


def _company_accounts():
    company_accounts = Account.query.filter(
        func.coalesce(Account.is_active, True) == True,
        func.lower(func.trim(Account.account_type)) == 'company'
    ).order_by(Account.name.asc()).all()
    if company_accounts:
        return company_accounts
    return Account.query.filter(func.coalesce(Account.is_active, True) == True).order_by(Account.name.asc()).all()


def _account_categories():
    return AccountCategory.query.filter_by(is_active=True).order_by(AccountCategory.name.asc()).all()


def _active_accounts():
    return Account.query.filter(func.coalesce(Account.is_active, True) == True)


def _is_account_active(account):
    return bool(account) and getattr(account, 'is_active', True) is not False


def _expected_account_category(method):
    m = (method or '').strip().lower()
    if m in ['cash', 'cash sale']:
        return 'cash'
    if m in ['bank', 'bank transfer', 'check', 'cheque', 'card', 'online']:
        return 'bank'
    return None


def _validate_account_matches_method(account, method, role_label):
    expected = _expected_account_category(method)
    if not expected:
        return
    acc_cat = (getattr(account, 'category', None) or '').strip().lower()
    if acc_cat != expected:
        raise ValueError(f"{role_label} account must be a {expected} account for method '{method}'.")


def _ensure_default_account_categories():
    defaults = ['Company', 'Own Funds', 'Clients', 'External', 'Loan']
    existing = {
        (row.name or '').strip().lower()
        for row in AccountCategory.query.filter_by(is_active=True).all()
    }
    created = False
    for name in defaults:
        if name.lower() in existing:
            continue
        db.session.add(AccountCategory(name=name))
        created = True
    if created:
        db.session.commit()


def _backfill_legacy_account_groups():
    changed = False
    for account in Account.query.filter(
        func.coalesce(Account.is_active, True) == True,
        or_(Account.source_category.is_(None), func.trim(Account.source_category) == '')
    ).all():
        account_type = (account.account_type or '').strip().lower()
        if 'client' in account_type:
            account.source_category = 'Clients'
        elif 'loan' in account_type:
            account.source_category = 'Loan'
        elif 'supplier' in account_type or 'external' in account_type:
            account.source_category = 'External'
        else:
            account.source_category = 'Company'
        changed = True
    if changed:
        db.session.commit()


def _apply_payment_to_pending_bills(client, paid_amount, discount_amount=0):
    """Apply receive amount + discount to open pending bills of a client."""
    if not client:
        return

    total_settlement = max(0.0, float(paid_amount or 0)) + max(0.0, float(discount_amount or 0))
    if total_settlement <= 0:
        return

    client_name_norm = (client.name or '').strip().lower()
    match_filters = [
        func.lower(func.trim(func.coalesce(PendingBill.client_name, ''))) == client_name_norm
    ]
    client_code_norm = (client.code or '').strip().lower()
    if client_code_norm:
        match_filters.append(
            func.lower(func.trim(func.coalesce(PendingBill.client_code, ''))) == client_code_norm
        )

    open_bills = PendingBill.query.filter(
        PendingBill.is_void == False,
        PendingBill.is_paid == False,
        PendingBill.amount > 0,
        or_(*match_filters)
    ).order_by(PendingBill.id.asc()).all()

    remaining = total_settlement
    for pb in open_bills:
        if remaining <= 0:
            break
        bill_amount = float(pb.amount or 0)
        if bill_amount <= 0:
            pb.is_paid = True
            continue
        settle = min(bill_amount, remaining)
        pb.amount = max(0.0, bill_amount - settle)
        pb.client_name = client.name
        pb.client_code = client.code
        pb.is_paid = pb.amount <= 0.00001
        remaining -= settle


@accounts_bp.route('/')
@login_required
def dashboard():
    """Accounts dashboard with KPI cards and financial overview."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    today = pk_today()
    
    # Calculate KPIs
    # Total ledger payments from clients today (Payment table).
    client_payments_today = db.session.query(func.sum(Payment.amount)).filter(
        Payment.date_posted >= today,
        Payment.date_posted < today + timedelta(days=1),
        Payment.is_void == False
    ).scalar() or 0
    
    # Total payments to suppliers today (SupplierPayment table + GRN paid_amount).
    supplier_payments_core_today = db.session.query(func.sum(SupplierPayment.amount)).filter(
        SupplierPayment.date_posted >= today,
        SupplierPayment.date_posted < today + timedelta(days=1),
        SupplierPayment.is_void == False
    ).scalar() or 0

    grn_supplier_paid_today = db.session.query(func.sum(GRN.paid_amount)).filter(
        GRN.date_posted >= today,
        GRN.date_posted < today + timedelta(days=1),
        GRN.is_void == False,
        GRN.paid_amount > 0
    ).scalar() or 0

    supplier_payments_today = float(supplier_payments_core_today or 0) + float(grn_supplier_paid_today or 0)
    
    # Total expenditures today
    expenditures_today = db.session.query(func.sum(FbmCashDrawerEntry.amount)).filter(
        FbmCashDrawerEntry.date_posted >= today,
        FbmCashDrawerEntry.date_posted < today + timedelta(days=1),
        FbmCashDrawerEntry.entry_type == 'out',
        FbmCashDrawerEntry.is_void == False
    ).scalar() or 0
    
    # Total receipts today (paid amounts only): bookings + direct sales.
    booking_paid_today = db.session.query(func.sum(Booking.paid_amount)).filter(
        Booking.date_posted >= today,
        Booking.date_posted < today + timedelta(days=1),
        Booking.is_void == False,
        Booking.paid_amount > 0
    ).scalar() or 0

    sales_paid_today = db.session.query(func.sum(DirectSale.paid_amount)).filter(
        DirectSale.date_posted >= today,
        DirectSale.date_posted < today + timedelta(days=1),
        DirectSale.is_void == False
    ).scalar() or 0

    receipts_today = float(booking_paid_today or 0) + float(sales_paid_today or 0)
    
    # Account balances + due clients + company liquidity KPI
    accounts = _active_accounts().order_by(Account.name.asc()).all()
    due_clients = _client_due_summary()
    supplier_payables = _supplier_payable_summary()
    company_accounts = _company_accounts()
    account_categories = _account_categories()
    total_company_money = sum(float(a.balance or 0) for a in company_accounts)
    total_cash_money = sum(float(a.balance or 0) for a in company_accounts if (a.category or '').lower() == 'cash')
    receive_source_accounts = [{
        'id': a.id,
        'name': a.name,
        'source_category': (a.source_category or '').strip()
    } for a in accounts if (a.source_category or '').strip()]
    
    suppliers = Supplier.query.filter_by(is_active=True).order_by(Supplier.name.asc()).all()
    clients = Client.query.filter_by(is_active=True).order_by(Client.name.asc()).all()
    return render_template('accounts/dashboard.html',
                          client_payments_today=client_payments_today,
                          supplier_payments_today=supplier_payments_today,
                          expenditures_today=expenditures_today,
                          receipts_today=receipts_today,
                         accounts=accounts,
                         due_clients=due_clients,
                         supplier_payables=supplier_payables,
                         company_accounts=company_accounts,
                          account_categories=account_categories,
                          receive_source_accounts=receive_source_accounts,
                          total_company_money=total_company_money,
                          total_cash_money=total_cash_money,
                          suppliers=suppliers,
                          clients=clients,
                          default_tx_datetime=pk_now().strftime('%Y-%m-%dT%H:%M'))


def _parse_date_range(default_days=30):
    """Parse `from`/`to` from query string. Returns (date_from, date_to_exclusive)."""
    from_raw = (request.args.get('from') or '').strip()
    to_raw = (request.args.get('to') or '').strip()
    today = pk_today()
    try:
        date_from = datetime.strptime(from_raw, '%Y-%m-%d').date() if from_raw else (today - timedelta(days=default_days))
    except ValueError:
        date_from = today - timedelta(days=default_days)
    try:
        date_to = datetime.strptime(to_raw, '%Y-%m-%d').date() if to_raw else today
    except ValueError:
        date_to = today
    if date_to < date_from:
        date_to = date_from
    return date_from, date_to + timedelta(days=1)


@accounts_bp.route('/payments/clients')
@login_required
def client_payments():
    """View and manage payments from clients."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = (request.args.get('q') or '').strip()
    method_f = (request.args.get('method') or '').strip()
    date_from, date_to_excl = _parse_date_range()

    q = Payment.query.filter(
        Payment.is_void == False,
        Payment.date_posted >= date_from,
        Payment.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Payment.client_name.ilike(like), Payment.note.ilike(like), Payment.account_name.ilike(like)))
    if method_f:
        q = q.filter(func.lower(Payment.method) == method_f.lower())

    total_amount = q.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar() or 0
    total_count = q.count()
    payments = q.order_by(Payment.date_posted.desc()).paginate(page=page, per_page=per_page)

    return render_template('accounts/client_payments.html', payments=payments,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1),
                           search=search, method_f=method_f,
                           total_amount=total_amount, total_count=total_count)


@accounts_bp.route('/payments/suppliers')
@login_required
def supplier_payments():
    """View and manage payments to suppliers."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = (request.args.get('q') or '').strip()
    method_f = (request.args.get('method') or '').strip()
    date_from, date_to_excl = _parse_date_range()

    q = SupplierPayment.query.filter(
        SupplierPayment.is_void == False,
        SupplierPayment.date_posted >= date_from,
        SupplierPayment.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.join(Supplier).filter(or_(Supplier.name.ilike(like), SupplierPayment.note.ilike(like), SupplierPayment.account_name.ilike(like)))
    if method_f:
        q = q.filter(func.lower(SupplierPayment.method) == method_f.lower())

    total_amount = q.with_entities(func.coalesce(func.sum(SupplierPayment.amount), 0)).scalar() or 0
    total_count = q.count()
    payments = q.order_by(SupplierPayment.date_posted.desc()).paginate(page=page, per_page=per_page)

    return render_template('accounts/supplier_payments.html', payments=payments,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1),
                           search=search, method_f=method_f,
                           total_amount=total_amount, total_count=total_count)


@accounts_bp.route('/expenditures')
@login_required
def expenditures():
    """View and manage personal expenditures."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = (request.args.get('q') or '').strip()
    category_f = (request.args.get('category') or '').strip()
    date_from, date_to_excl = _parse_date_range()

    q = FbmCashDrawerEntry.query.filter(
        FbmCashDrawerEntry.entry_type == 'out',
        FbmCashDrawerEntry.is_void == False,
        FbmCashDrawerEntry.date_posted >= date_from,
        FbmCashDrawerEntry.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.filter(or_(FbmCashDrawerEntry.note.ilike(like), FbmCashDrawerEntry.category.ilike(like)))
    if category_f:
        q = q.filter(FbmCashDrawerEntry.category == category_f)

    total_amount = q.with_entities(func.coalesce(func.sum(FbmCashDrawerEntry.amount), 0)).scalar() or 0
    total_count = q.count()
    expenditures = q.order_by(FbmCashDrawerEntry.date_posted.desc()).paginate(page=page, per_page=per_page)

    categories = [r[0] for r in db.session.query(FbmCashDrawerEntry.category).filter(
        FbmCashDrawerEntry.entry_type == 'out',
        FbmCashDrawerEntry.is_void == False,
        FbmCashDrawerEntry.category.isnot(None),
        FbmCashDrawerEntry.category != ''
    ).distinct().order_by(FbmCashDrawerEntry.category).all()]

    return render_template('accounts/expenditures.html', expenditures=expenditures,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1),
                           search=search, category_f=category_f, categories=categories,
                           total_amount=total_amount, total_count=total_count)


@accounts_bp.route('/receipts')
@login_required
def receipts():
    """View receipts from sales and GRN paid amounts within a date range."""
    date_from, date_to_excl = _parse_date_range(default_days=0)

    sales = DirectSale.query.filter(
        DirectSale.date_posted >= date_from,
        DirectSale.date_posted < date_to_excl,
        DirectSale.is_void == False
    ).order_by(DirectSale.date_posted.desc()).all()

    grns = GRN.query.filter(
        GRN.date_posted >= date_from,
        GRN.date_posted < date_to_excl,
        GRN.is_void == False,
        GRN.paid_amount > 0
    ).order_by(GRN.date_posted.desc()).all()

    return render_template('accounts/receipts.html', sales=sales, grns=grns,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1))


@accounts_bp.route('/accounts')
@login_required
def manage_accounts():
    """Manage financial accounts."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    accounts = _active_accounts().order_by(Account.name).all()
    categories = _account_categories()
    
    # Group accounts by category and type for better organization
    account_summary = {}
    for account in accounts:
        category_name = (account.category or 'Unknown').upper()
        account_type_name = account.account_type or 'Unknown'
        key = f"{category_name} - {account_type_name}"
        if key not in account_summary:
            account_summary[key] = []
        account_summary[key].append(account)
    
    return render_template('accounts/manage_accounts.html', accounts=accounts, account_summary=account_summary, categories=categories)


@accounts_bp.route('/categories/add', methods=['POST'])
@login_required
def add_account_category():
    name = (request.form.get('name') or '').strip()
    note = (request.form.get('note') or '').strip()

    if not name:
        flash('Category name is required.', 'danger')
        return redirect(url_for('accounts.manage_accounts'))

    existing = AccountCategory.query.filter(
        func.lower(func.trim(AccountCategory.name)) == name.lower(),
        AccountCategory.is_active == True
    ).first()
    if existing:
        flash('This account category already exists.', 'warning')
        return redirect(url_for('accounts.manage_accounts'))

    db.session.add(AccountCategory(name=name, note=note or None))
    db.session.commit()
    audit_log(current_user, g.tenant_id, 'account.category.create', f'name={name}')
    flash('Account category created successfully.', 'success')
    return redirect(url_for('accounts.manage_accounts'))


@accounts_bp.route('/accounts/add', methods=['GET', 'POST'])
@login_required
def add_account():
    """Add a new account."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        category = (request.form.get('category') or '').strip().lower()
        source_category = (request.form.get('source_category') or '').strip()
        account_type = (request.form.get('account_type') or '').strip()
        note = request.form.get('note')
        initial_balance_raw = request.form.get('initial_balance', '0')

        if not name:
            flash('Account name is required.', 'danger')
            return redirect(url_for('accounts.add_account'))
        if category not in ('cash', 'bank'):
            flash('Please select Cash or Bank.', 'danger')
            return redirect(url_for('accounts.add_account'))
        if not account_type:
            flash('Please select an account type.', 'danger')
            return redirect(url_for('accounts.add_account'))
        if not source_category:
            flash('Please select an account category first.', 'danger')
            return redirect(url_for('accounts.add_account'))
        category_exists = AccountCategory.query.filter(
            func.lower(func.trim(AccountCategory.name)) == source_category.lower(),
            AccountCategory.is_active == True
        ).first()
        if not category_exists:
            flash('Please select a valid account category.', 'danger')
            return redirect(url_for('accounts.add_account'))

        try:
            initial_balance = float(initial_balance_raw or 0)
        except ValueError:
            flash('Initial balance must be a valid number.', 'danger')
            return redirect(url_for('accounts.add_account'))

        if category == 'bank':
            bank_name = (request.form.get('bank_name') or '').strip()
            account_holder_name = (request.form.get('account_holder_name') or '').strip()
            account_number = (request.form.get('account_number') or '').strip()
            branch_code = (request.form.get('branch_code') or '').strip()
            if not bank_name or not account_holder_name or not account_number:
                flash('Bank account name, holder and number are required for bank accounts.', 'danger')
                return redirect(url_for('accounts.add_account'))
        else:
            bank_name = None
            account_holder_name = None
            account_number = None
            branch_code = None

        account = Account(
            name=name,
            category=category,
            source_category=category_exists.name,
            account_type=account_type,
            type=account_type,
            balance=initial_balance,
            bank_name=bank_name,
            account_holder_name=account_holder_name,
            account_number=account_number,
            branch_code=branch_code,
            note=note
        )
        try:
            db.session.add(account)
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            flash(f'Unable to add account due to database constraint: {exc.orig}', 'danger')
            return redirect(url_for('accounts.add_account'))
        except Exception as exc:
            db.session.rollback()
            logger.exception('Add account failed')
            flash(f'Unable to add account: {exc}', 'danger')
            return redirect(url_for('accounts.add_account'))

        audit_log(current_user, g.tenant_id, 'account.create', f'name={name}, category={category}, source_category={category_exists.name}, account_type={account_type}')
        flash('Account added successfully!', 'success')
        return redirect(url_for('accounts.manage_accounts'))

    return render_template('accounts/add_account.html', categories=_account_categories())


@accounts_bp.route('/transfers')
@login_required
def transfers():
    """View account transfers."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = (request.args.get('q') or '').strip()
    date_from, date_to_excl = _parse_date_range()

    q = AccountTransaction.query.filter(
        AccountTransaction.is_void == False,
        AccountTransaction.transaction_type == 'Transfer',
        AccountTransaction.date_posted >= date_from,
        AccountTransaction.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.filter(or_(AccountTransaction.description.ilike(like), AccountTransaction.note.ilike(like)))

    total_amount = q.with_entities(func.coalesce(func.sum(AccountTransaction.amount), 0)).scalar() or 0
    total_count = q.count()
    transfers = q.order_by(AccountTransaction.date_posted.desc()).paginate(page=page, per_page=per_page)

    return render_template('accounts/transfers.html', transfers=transfers,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1),
                           search=search, total_amount=total_amount, total_count=total_count)


@accounts_bp.route('/ledger/<int:account_id>')
@login_required
def account_ledger(account_id):
    account = Account.query.get_or_404(account_id)
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = (request.args.get('q') or '').strip()
    type_f = (request.args.get('type') or '').strip()
    show_voided = request.args.get('show_voided') == '1'
    date_from, date_to_excl = _parse_date_range(default_days=90)

    base_filters = [
        or_(AccountTransaction.from_account_id == account.id,
            AccountTransaction.to_account_id == account.id)
    ]
    if not show_voided:
        base_filters.append(AccountTransaction.is_void == False)

    # Opening balance = current balance - net effect of (active) transactions in/after date_from
    after_in = db.session.query(func.coalesce(func.sum(AccountTransaction.amount), 0)).filter(
        AccountTransaction.is_void == False,
        AccountTransaction.to_account_id == account.id,
        AccountTransaction.date_posted >= date_from
    ).scalar() or 0
    after_out = db.session.query(func.coalesce(func.sum(AccountTransaction.amount), 0)).filter(
        AccountTransaction.is_void == False,
        AccountTransaction.from_account_id == account.id,
        AccountTransaction.date_posted >= date_from
    ).scalar() or 0
    opening_balance = float(account.balance or 0) - float(after_in or 0) + float(after_out or 0)

    q = AccountTransaction.query.filter(*base_filters,
        AccountTransaction.date_posted >= date_from,
        AccountTransaction.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.filter(or_(AccountTransaction.description.ilike(like), AccountTransaction.note.ilike(like)))
    if type_f:
        q = q.filter(AccountTransaction.transaction_type == type_f)

    period_in = q.with_entities(func.coalesce(func.sum(AccountTransaction.amount), 0)).filter(
        AccountTransaction.to_account_id == account.id, AccountTransaction.is_void == False
    ).scalar() or 0
    period_out = q.with_entities(func.coalesce(func.sum(AccountTransaction.amount), 0)).filter(
        AccountTransaction.from_account_id == account.id, AccountTransaction.is_void == False
    ).scalar() or 0

    # Fetch ALL rows in window (chronological asc) so we can compute running balance
    rows_asc = q.order_by(AccountTransaction.date_posted.asc(), AccountTransaction.id.asc()).all()
    running = opening_balance
    enriched = []
    for r in rows_asc:
        delta = 0.0
        if not r.is_void:
            if r.to_account_id == account.id:
                delta += float(r.amount or 0)
            if r.from_account_id == account.id:
                delta -= float(r.amount or 0)
        running += delta
        enriched.append({'tx': r, 'delta': delta, 'running': running})

    enriched.reverse()  # display newest first

    # Manual pagination over enriched list
    total_rows = len(enriched)
    start = (page - 1) * per_page
    end = start + per_page
    page_rows = enriched[start:end]

    types = ['Receipt', 'Payment', 'Transfer', 'Supplier Payment', 'Expense', 'Loss', 'Adjustment']

    return render_template('accounts/account_ledger.html', account=account, page_rows=page_rows,
                           opening_balance=opening_balance, period_in=period_in, period_out=period_out,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1),
                           search=search, type_f=type_f, types=types, show_voided=show_voided,
                           page=page, per_page=per_page, total_rows=total_rows,
                           has_prev=page > 1, has_next=end < total_rows)


def _reverse_balance_effect(tx):
    """Reverse the balance effect of an AccountTransaction (used for voiding)."""
    if tx.from_account_id:
        a = Account.query.get(tx.from_account_id)
        if a:
            a.balance = float(a.balance or 0) + float(tx.amount or 0)
    if tx.to_account_id:
        a = Account.query.get(tx.to_account_id)
        if a:
            a.balance = float(a.balance or 0) - float(tx.amount or 0)


@accounts_bp.route('/transactions/<int:tx_id>/void', methods=['POST'])
@login_required
def void_transaction(tx_id):
    """Void an AccountTransaction and reverse its balance effect.
    If the transaction is linked to a Payment/SupplierPayment by date+amount+account, void those too."""
    tx = AccountTransaction.query.get_or_404(tx_id)
    if tx.is_void:
        flash('Transaction is already voided.', 'warning')
        return redirect(request.referrer or url_for('accounts.dashboard'))

    reason = (request.form.get('reason') or '').strip()
    try:
        _reverse_balance_effect(tx)
        tx.is_void = True
        if reason:
            tx.note = ((tx.note or '') + f' | VOIDED: {reason}').strip(' |')

        # Prefer deterministic linkage via SRC markers when present (used by main.py sync helpers).
        note_txt = (tx.note or '')
        pay_match = re.search(r'\[SRC:Payment:(\d+)\]', note_txt, flags=re.IGNORECASE)
        if pay_match:
            p = Payment.query.get(int(pay_match.group(1)))
            if p and not bool(getattr(p, 'is_void', False)):
                p.is_void = True
        sp_match = re.search(r'\[SRC:SupplierPayment:(\d+)\]', note_txt, flags=re.IGNORECASE)
        if sp_match:
            sp = SupplierPayment.query.get(int(sp_match.group(1)))
            if sp and not bool(getattr(sp, 'is_void', False)):
                sp.is_void = True

        # Best-effort cascade: link by transaction_type + amount + date_posted (within ±2 sec)
        from_acc = Account.query.get(tx.from_account_id) if tx.from_account_id else None
        to_acc = Account.query.get(tx.to_account_id) if tx.to_account_id else None
        time_lo = tx.date_posted - timedelta(seconds=5) if tx.date_posted else None
        time_hi = tx.date_posted + timedelta(seconds=5) if tx.date_posted else None

        if tx.transaction_type == 'Receipt' and to_acc and time_lo:
            p = Payment.query.filter(
                Payment.is_void == False,
                Payment.amount == tx.amount,
                Payment.account_name == to_acc.name,
                Payment.date_posted >= time_lo,
                Payment.date_posted <= time_hi
            ).first()
            if p:
                p.is_void = True
        elif tx.transaction_type == 'Supplier Payment' and from_acc and time_lo:
            sp = SupplierPayment.query.filter(
                SupplierPayment.is_void == False,
                SupplierPayment.amount == tx.amount,
                SupplierPayment.account_name == from_acc.name,
                SupplierPayment.date_posted >= time_lo,
                SupplierPayment.date_posted <= time_hi
            ).first()
            if sp:
                sp.is_void = True

        db.session.commit()
        audit_log(current_user, g.tenant_id, 'account.transaction.void',
                  f'tx_id={tx.id}, type={tx.transaction_type}, amount={tx.amount}, reason={reason}')
        flash('Transaction voided and balances corrected.', 'success')
    except Exception as exc:
        db.session.rollback()
        logger.exception('Void transaction failed')
        flash(f'Unable to void transaction: {exc}', 'danger')

    return redirect(request.referrer or url_for('accounts.dashboard'))


@accounts_bp.route('/<int:account_id>/data')
@login_required
def account_data(account_id):
    """JSON data for the edit account modal."""
    a = Account.query.get_or_404(account_id)
    return jsonify({
        'id': a.id,
        'name': a.name,
        'category': a.category,
        'source_category': a.source_category,
        'account_type': a.account_type or (getattr(a, 'type', None) or ''),
        'balance': float(a.balance or 0),
        'bank_name': a.bank_name or '',
        'account_holder_name': a.account_holder_name or '',
        'account_number': a.account_number or '',
        'branch_code': a.branch_code or '',
        'note': a.note or '',
        'is_active': bool(a.is_active),
    })


@accounts_bp.route('/<int:account_id>/edit', methods=['POST'])
@login_required
def edit_account(account_id):
    """Edit an account's metadata. Balance changes are recorded as Adjustment transactions."""
    a = Account.query.get_or_404(account_id)
    try:
        name = (request.form.get('name') or '').strip()
        category = (request.form.get('category') or '').strip().lower()
        source_category = (request.form.get('source_category') or '').strip()
        account_type = (request.form.get('account_type') or '').strip()
        note = (request.form.get('note') or '').strip()
        new_balance_raw = request.form.get('balance', '').strip()

        if not name:
            raise ValueError('Account name is required.')
        if category not in ('cash', 'bank'):
            raise ValueError('Please select Cash or Bank.')
        if not account_type:
            raise ValueError('Please select an account type.')
        if not source_category:
            raise ValueError('Please select an account category.')

        cat = AccountCategory.query.filter(
            func.lower(func.trim(AccountCategory.name)) == source_category.lower(),
            AccountCategory.is_active == True
        ).first()
        if not cat:
            raise ValueError('Selected account category not found.')

        a.name = name
        a.category = category
        a.source_category = cat.name
        a.account_type = account_type
        a.type = account_type
        a.note = note or None

        if category == 'bank':
            a.bank_name = (request.form.get('bank_name') or '').strip() or None
            a.account_holder_name = (request.form.get('account_holder_name') or '').strip() or None
            a.account_number = (request.form.get('account_number') or '').strip() or None
            a.branch_code = (request.form.get('branch_code') or '').strip() or None
        else:
            a.bank_name = None
            a.account_holder_name = None
            a.account_number = None
            a.branch_code = None

        if new_balance_raw != '':
            try:
                new_balance = float(new_balance_raw)
            except ValueError:
                raise ValueError('Balance must be a valid number.')
            old_balance = float(a.balance or 0)
            diff = round(new_balance - old_balance, 2)
            if abs(diff) > 0.001:
                # Record as adjustment transaction so the trail shows it
                if diff > 0:
                    adj = AccountTransaction(
                        from_account_id=None, to_account_id=a.id, amount=abs(diff),
                        description='Balance adjustment (manual edit)',
                        note=f'Adjusted from Rs. {old_balance:.2f} to Rs. {new_balance:.2f}',
                        transaction_type='Adjustment', date_posted=pk_now()
                    )
                else:
                    adj = AccountTransaction(
                        from_account_id=a.id, to_account_id=None, amount=abs(diff),
                        description='Balance adjustment (manual edit)',
                        note=f'Adjusted from Rs. {old_balance:.2f} to Rs. {new_balance:.2f}',
                        transaction_type='Adjustment', date_posted=pk_now()
                    )
                db.session.add(adj)
                a.balance = new_balance

        db.session.commit()
        audit_log(current_user, g.tenant_id, 'account.update', f'id={a.id}, name={a.name}')
        flash('Account updated successfully.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception as exc:
        db.session.rollback()
        logger.exception('Edit account failed')
        flash(f'Unable to update account: {exc}', 'danger')

    return redirect(url_for('accounts.manage_accounts'))


@accounts_bp.route('/<int:account_id>/toggle', methods=['POST'])
@login_required
def toggle_account(account_id):
    """Soft-deactivate / reactivate an account."""
    a = Account.query.get_or_404(account_id)
    a.is_active = not bool(a.is_active)
    db.session.commit()
    audit_log(current_user, g.tenant_id, 'account.toggle', f'id={a.id}, name={a.name}, active={a.is_active}')
    flash(f'Account {"reactivated" if a.is_active else "deactivated"}.', 'success')
    return redirect(url_for('accounts.manage_accounts'))


@accounts_bp.route('/audit')
@login_required
def audit_trail():
    """Full audit trail across all account-affecting transactions."""
    page = request.args.get('page', 1, type=int)
    per_page = 60
    search = (request.args.get('q') or '').strip()
    type_f = (request.args.get('type') or '').strip()
    account_id_f = request.args.get('account_id', type=int)
    show_voided = request.args.get('show_voided') == '1'
    date_from, date_to_excl = _parse_date_range(default_days=30)

    q = AccountTransaction.query.filter(
        AccountTransaction.date_posted >= date_from,
        AccountTransaction.date_posted < date_to_excl
    )
    if not show_voided:
        q = q.filter(AccountTransaction.is_void == False)
    if search:
        like = f'%{search}%'
        q = q.filter(or_(AccountTransaction.description.ilike(like), AccountTransaction.note.ilike(like)))
    if type_f:
        q = q.filter(AccountTransaction.transaction_type == type_f)
    if account_id_f:
        q = q.filter(or_(AccountTransaction.from_account_id == account_id_f,
                         AccountTransaction.to_account_id == account_id_f))

    total_in = q.with_entities(func.coalesce(func.sum(AccountTransaction.amount), 0)).filter(
        AccountTransaction.to_account_id.isnot(None), AccountTransaction.is_void == False
    ).scalar() or 0
    total_out = q.with_entities(func.coalesce(func.sum(AccountTransaction.amount), 0)).filter(
        AccountTransaction.from_account_id.isnot(None), AccountTransaction.is_void == False
    ).scalar() or 0

    rows = q.order_by(AccountTransaction.date_posted.desc(), AccountTransaction.id.desc()).paginate(page=page, per_page=per_page)
    types = ['Receipt', 'Payment', 'Transfer', 'Supplier Payment', 'Expense', 'Loss', 'Adjustment']
    accounts = _active_accounts().order_by(Account.name.asc()).all()

    return render_template('accounts/audit.html', rows=rows, accounts=accounts, types=types,
                           date_from=date_from, date_to=date_to_excl - timedelta(days=1),
                           search=search, type_f=type_f, account_id_f=account_id_f,
                           show_voided=show_voided, total_in=total_in, total_out=total_out)


@accounts_bp.route('/transactions/new', methods=['POST'])
@login_required
def add_transaction():
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    tx_mode = (request.form.get('tx_mode') or '').strip().lower()
    note = (request.form.get('note') or '').strip()
    method = (request.form.get('method') or 'Cash').strip()
    tx_date_raw = (request.form.get('date_posted') or '').strip()
    tx_date = pk_now()
    if tx_date_raw:
        try:
            tx_date = datetime.strptime(tx_date_raw, '%Y-%m-%dT%H:%M')
        except ValueError:
            tx_date = pk_now()

    try:
        if tx_mode == 'receive':
            receive_account_id = request.form.get('receive_account_id', type=int)
            receive_from_category = (request.form.get('receive_from_category') or 'client_ledger').strip()
            client_input = (request.form.get('client_input') or '').strip()
            receive_from_account_id = request.form.get('receive_from_account_id', type=int)
            receive_source_label = (request.form.get('receive_source_label') or '').strip()
            amount = float(request.form.get('amount', 0) or 0)
            discount = float(request.form.get('discount', 0) or 0)

            if amount <= 0:
                raise ValueError('Received amount must be greater than zero.')
            if discount < 0:
                raise ValueError('Discount cannot be negative.')

            receive_account = Account.query.get(receive_account_id) if receive_account_id else None
            if not _is_account_active(receive_account):
                raise ValueError('Please select a valid destination account.')
            _validate_account_matches_method(receive_account, method, 'Destination')

            receive_account.balance = float(receive_account.balance or 0) + amount

            if receive_from_category == 'client_ledger':
                client = _resolve_client(client_input)
                if not client:
                    raise ValueError('Client not found. Please select a valid client from the dues list.')

                payment = Payment(
                    client_name=client.name,
                    amount=amount,
                    method=method or 'Cash',
                    note=note,
                    discount=discount,
                    discount_reason='Accounts receive transaction',
                    date_posted=tx_date,
                    account_name=receive_account.name,
                    bank_name=(receive_account.bank_name or ''),
                    account_no=(receive_account.account_number or ''),
                    payment_account_id=receive_account.id
                )
                db.session.add(payment)
                db.session.flush()
                pay_marker = f"[SRC:Payment:{payment.id}]"

                account_tx = AccountTransaction(
                    from_account_id=None,
                    to_account_id=receive_account.id,
                    amount=amount,
                    description=f"Client payment received from {client.name}",
                    note=" ".join([x for x in [(note or '').strip(), pay_marker] if x]).strip(),
                    transaction_type='Receipt',
                    date_posted=tx_date
                )
                db.session.add(account_tx)

                if discount > 0:
                    discount_tx = AccountTransaction(
                        from_account_id=None,
                        to_account_id=None,
                        amount=discount,
                        description=f"Waive-off loss for {client.name}",
                        note=" ".join([x for x in [(note or 'Discount as company loss').strip(), f"{pay_marker}:LOSS"] if x]).strip(),
                        transaction_type='Loss',
                        date_posted=tx_date
                    )
                    db.session.add(discount_tx)

                _apply_payment_to_pending_bills(client, amount, discount)
                audit_log(current_user, g.tenant_id, 'account.transaction.receive', f'source_category=client_ledger, client={client.name}, account={receive_account.name}, amount={amount}, discount={discount}')

            elif receive_from_category == 'other_source':
                if not receive_source_label:
                    raise ValueError('Please enter who or what this money was received from.')

                account_tx = AccountTransaction(
                    from_account_id=None,
                    to_account_id=receive_account.id,
                    amount=amount,
                    description=f"Money received from {receive_source_label}",
                    note=note,
                    transaction_type='Receipt',
                    date_posted=tx_date
                )
                db.session.add(account_tx)
                audit_log(current_user, g.tenant_id, 'account.transaction.receive', f'source_category=other_source, source={receive_source_label}, to={receive_account.name}, amount={amount}')

            else:
                category_exists = AccountCategory.query.filter(
                    func.lower(func.trim(AccountCategory.name)) == receive_from_category.lower(),
                    AccountCategory.is_active == True
                ).first()
                if not category_exists:
                    raise ValueError('Please select a valid receive source category.')

                from_account = Account.query.get(receive_from_account_id) if receive_from_account_id else None
                if not _is_account_active(from_account):
                    raise ValueError('Please select a valid source account.')
                if (from_account.source_category or '').strip().lower() != category_exists.name.lower():
                    raise ValueError('Selected source account does not belong to the chosen category.')
                if from_account.id == receive_account.id:
                    raise ValueError('Source and destination accounts cannot be the same.')
                if float(from_account.balance or 0) < amount:
                    raise ValueError('Insufficient balance in selected source account.')

                from_account.balance = float(from_account.balance or 0) - amount
                account_tx = AccountTransaction(
                    from_account_id=from_account.id,
                    to_account_id=receive_account.id,
                    amount=amount,
                    description=f"Funds received from account {from_account.name}",
                    note=note,
                    transaction_type='Transfer',
                    date_posted=tx_date
                )
                db.session.add(account_tx)
                audit_log(current_user, g.tenant_id, 'account.transaction.receive', f'source_category={category_exists.name}, from={from_account.name}, to={receive_account.name}, amount={amount}')

            db.session.commit()
            flash('Receive transaction recorded successfully.', 'success')

        elif tx_mode == 'pay':
            from_account_id = request.form.get('pay_from_account_id', type=int)
            to_account_id = request.form.get('pay_to_account_id', type=int)
            pay_target = (request.form.get('pay_target') or '').strip().lower()
            amount = float(request.form.get('amount', 0) or 0)

            if amount <= 0:
                raise ValueError('Payment amount must be greater than zero.')

            from_account = Account.query.get(from_account_id) if from_account_id else None
            if not _is_account_active(from_account):
                raise ValueError('Please select a valid source account.')
            _validate_account_matches_method(from_account, method, 'Source')
            if float(from_account.balance or 0) < amount:
                raise ValueError('Insufficient balance in selected source account.')

            from_account.balance = float(from_account.balance or 0) - amount

            if pay_target == 'company_transfer':
                to_account = Account.query.get(to_account_id) if to_account_id else None
                if not _is_account_active(to_account):
                    raise ValueError('Please select a valid destination account.')
                if to_account.id == from_account.id:
                    raise ValueError('Source and destination accounts cannot be the same.')

                to_account.balance = float(to_account.balance or 0) + amount
                tx = AccountTransaction(
                    from_account_id=from_account.id,
                    to_account_id=to_account.id,
                    amount=amount,
                    description='Intra-company transfer',
                    note=note,
                    transaction_type='Transfer',
                    date_posted=tx_date
                )
                db.session.add(tx)
                audit_log(current_user, g.tenant_id, 'account.transaction.transfer', f'from={from_account.name}, to={to_account.name}, amount={amount}')
                flash('Transfer transaction recorded successfully.', 'success')

            elif pay_target == 'supplier':
                supplier_id = request.form.get('supplier_id', type=int)
                supplier_input = (request.form.get('supplier_input') or '').strip()
                supplier = Supplier.query.get(supplier_id) if supplier_id else None
                if not supplier and supplier_input:
                    supplier = _resolve_supplier(supplier_input)
                if not supplier:
                    raise ValueError('Please select a valid supplier.')

                sp = SupplierPayment(
                    supplier_id=supplier.id,
                    amount=amount,
                    method=method or 'Cash',
                    note=note,
                    date_posted=tx_date,
                    bank_name=(from_account.bank_name or ''),
                    account_name=(from_account.account_holder_name or from_account.name or ''),
                    account_no=(from_account.account_number or ''),
                    payment_account_id=from_account.id
                )
                db.session.add(sp)
                db.session.flush()
                sp_marker = f"[SRC:SupplierPayment:{sp.id}]"

                tx = AccountTransaction(
                    from_account_id=from_account.id,
                    to_account_id=None,
                    amount=amount,
                    description=f'Supplier payment to {supplier.name}',
                    note=" ".join([x for x in [(note or '').strip(), sp_marker] if x]).strip(),
                    transaction_type='Supplier Payment',
                    date_posted=tx_date
                )
                db.session.add(tx)
                audit_log(current_user, g.tenant_id, 'account.transaction.supplier_payment', f'from={from_account.name}, supplier={supplier.name}, amount={amount}')
                flash('Supplier payment recorded successfully.', 'success')

            else:
                target_label = (request.form.get('target_label') or '').strip()
                if not target_label:
                    if pay_target == 'loan':
                        target_label = 'Loan Payment'
                    elif pay_target == 'personal_expense':
                        target_label = 'Personal Expense'
                    else:
                        target_label = 'Other Payment'

                tx_type = 'Expense' if pay_target in ['personal_expense', 'other_expense'] else 'Payment'
                tx = AccountTransaction(
                    from_account_id=from_account.id,
                    to_account_id=None,
                    amount=amount,
                    description=target_label,
                    note=note,
                    transaction_type=tx_type,
                    date_posted=tx_date
                )
                db.session.add(tx)
                audit_log(current_user, g.tenant_id, 'account.transaction.pay', f'from={from_account.name}, target={target_label}, amount={amount}')
                flash('Outgoing payment recorded successfully.', 'success')

            db.session.commit()
        else:
            raise ValueError('Invalid transaction type selected.')

    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception as exc:
        db.session.rollback()
        logger.exception('Accounts transaction save failed')
        flash(f'Unable to save transaction: {exc}', 'danger')

    return redirect(url_for('accounts.dashboard'))


@accounts_bp.route('/transfers/add', methods=['GET', 'POST'])
@login_required
def add_transfer():
    """Add a new account transfer."""
    if request.method == 'POST':
        from_account_id = request.form.get('from_account')
        to_account_id = request.form.get('to_account')
        amount = float(request.form.get('amount'))
        description = request.form.get('description')
        note = request.form.get('note')
        
        if from_account_id and to_account_id and amount > 0:
            # Update account balances
            from_account = db.session.get(Account, int(from_account_id))
            to_account = db.session.get(Account, int(to_account_id))
            
            if from_account and to_account and from_account.balance >= amount:
                from_account.balance -= amount
                to_account.balance += amount
                
                transaction = AccountTransaction(
                    from_account_id=from_account_id,
                    to_account_id=to_account_id,
                    amount=amount,
                    description=description,
                    note=note,
                    transaction_type='Transfer'
                )
                db.session.add(transaction)
                db.session.commit()
                
                audit_log(current_user, g.tenant_id, 'account.transfer', f'from={from_account.name}, to={to_account.name}, amount={amount}')
                flash('Transfer completed successfully!', 'success')
            else:
                flash('Insufficient balance or invalid accounts!', 'danger')
        else:
            flash('Invalid transfer details!', 'danger')
        
        return redirect(url_for('accounts.transfers'))
    
    accounts = _active_accounts().all()
    return render_template('accounts/add_transfer.html', accounts=accounts)


# API endpoints for drill-down
@accounts_bp.route('/api/kpi/client_payments')
@login_required
def api_client_payments_today():
    """API endpoint for client payments KPI drill-down."""
    today = pk_today()
    
    payments = Payment.query.filter(
        Payment.date_posted >= today,
        Payment.date_posted < today + timedelta(days=1),
        Payment.is_void == False
    ).order_by(Payment.date_posted.desc()).all()
    
    data = [{
        'id': p.id,
        'client_name': p.client_name,
        'amount': p.amount,
        'method': p.method,
        'date_posted': p.date_posted.strftime('%Y-%m-%d %H:%M'),
        'note': p.note
    } for p in payments]
    
    return jsonify(data)


@accounts_bp.route('/api/kpi/supplier_payments')
@login_required
def api_supplier_payments_today():
    """API endpoint for supplier payments KPI drill-down."""
    today = pk_today()
    
    payments = SupplierPayment.query.filter(
        SupplierPayment.date_posted >= today,
        SupplierPayment.date_posted < today + timedelta(days=1),
        SupplierPayment.is_void == False
    ).join(Supplier).order_by(SupplierPayment.date_posted.desc()).all()
    
    data = [{
        'type': 'Supplier Payment',
        'id': p.id,
        'supplier_name': p.supplier.name if p.supplier else '',
        'amount': p.amount,
        'method': p.method,
        'date_posted': p.date_posted.strftime('%Y-%m-%d %H:%M'),
        'note': p.note,
        'account_name': p.account_name
    } for p in payments]

    grns = GRN.query.filter(
        GRN.date_posted >= today,
        GRN.date_posted < today + timedelta(days=1),
        GRN.is_void == False,
        GRN.paid_amount > 0
    ).order_by(GRN.date_posted.desc()).all()

    for g in grns:
        data.append({
            'type': 'GRN Purchase Payment',
            'id': g.id,
            'supplier_name': (g.supplier_rel.name if getattr(g, 'supplier_rel', None) else (g.supplier or '')),
            'amount': float(g.paid_amount or 0),
            'method': g.payment_type or '',
            'date_posted': g.date_posted.strftime('%Y-%m-%d %H:%M'),
            'note': g.note,
            'account_name': g.account_name
        })
    
    return jsonify(data)


@accounts_bp.route('/api/kpi/expenditures')
@login_required
def api_expenditures_today():
    """API endpoint for expenditures KPI drill-down."""
    today = pk_today()
    
    expenditures = FbmCashDrawerEntry.query.filter(
        FbmCashDrawerEntry.date_posted >= today,
        FbmCashDrawerEntry.date_posted < today + timedelta(days=1),
        FbmCashDrawerEntry.entry_type == 'out',
        FbmCashDrawerEntry.is_void == False
    ).order_by(FbmCashDrawerEntry.date_posted.desc()).all()
    
    data = [{
        'id': e.id,
        'amount': e.amount,
        'category': e.category,
        'method': e.method,
        'date_posted': e.date_posted.strftime('%Y-%m-%d %H:%M'),
        'note': e.note
    } for e in expenditures]
    
    return jsonify(data)


@accounts_bp.route('/api/kpi/receipts')
@login_required
def api_receipts_today():
    """API endpoint for receipts KPI drill-down."""
    today = pk_today()
    
    # Direct sales receipts (paid amounts only)
    sales = DirectSale.query.filter(
        DirectSale.date_posted >= today,
        DirectSale.date_posted < today + timedelta(days=1),
        DirectSale.is_void == False,
        DirectSale.paid_amount > 0
    ).order_by(DirectSale.date_posted.desc()).all()
    
    sales_data = [{
        'type': 'Direct Sale Receipt',
        'client_name': s.client_name,
        'amount': float(s.paid_amount or 0),
        'date_posted': s.date_posted.strftime('%Y-%m-%d %H:%M'),
        'note': s.note
    } for s in sales]
    
    # Booking receipts (paid amounts only)
    bookings = Booking.query.filter(
        Booking.date_posted >= today,
        Booking.date_posted < today + timedelta(days=1),
        Booking.is_void == False,
        Booking.paid_amount > 0
    ).order_by(Booking.date_posted.desc()).all()

    booking_data = [{
        'type': 'Booking Receipt',
        'client_name': b.client_name,
        'amount': float(b.paid_amount or 0),
        'date_posted': b.date_posted.strftime('%Y-%m-%d %H:%M'),
        'note': getattr(b, 'note', None)
    } for b in bookings]
    
    return jsonify(booking_data + sales_data)


@accounts_bp.route('/api/kpi/company_money')
@login_required
def api_company_money():
    accounts = _company_accounts()
    data = [{
        'id': a.id,
        'name': a.name,
        'category': a.category,
        'account_type': a.account_type,
        'balance': float(a.balance or 0)
    } for a in accounts]
    return jsonify(data)


@accounts_bp.route('/kpi/client_payments')
@login_required
def kpi_client_payments():
    """KPI drill-down page: payments received from clients."""
    date_from, date_to_excl = _parse_date_range(default_days=0)
    search = (request.args.get('q') or '').strip()
    method_f = (request.args.get('method') or '').strip()

    q = Payment.query.filter(
        Payment.is_void == False,
        Payment.date_posted >= date_from,
        Payment.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Payment.client_name.ilike(like), Payment.note.ilike(like), Payment.account_name.ilike(like)))
    if method_f:
        q = q.filter(func.lower(Payment.method) == method_f.lower())

    payments = q.order_by(Payment.date_posted.desc(), Payment.id.desc()).all()
    total_amount = float(sum(float(p.amount or 0) for p in payments))

    items = []
    for p in payments:
        client_obj = _resolve_client(p.client_name or '')
        items.append({
            'date_posted': p.date_posted,
            'party': p.client_name or '',
            'amount': float(p.amount or 0),
            'method': p.method or '',
            'account_name': p.account_name or '',
            'bill_no': p.manual_bill_no or p.auto_bill_no or '',
            'note': p.note or '',
            'link': (url_for('client_ledger', id=client_obj.id) if client_obj else None),
        })

    return render_template(
        'accounts/kpi_client_payments.html',
        items=items,
        total_amount=total_amount,
        total_count=len(items),
        date_from=date_from,
        date_to=date_to_excl - timedelta(days=1),
        search=search,
        method_f=method_f
    )


@accounts_bp.route('/kpi/supplier_payments')
@login_required
def kpi_supplier_payments():
    """KPI drill-down page: payments made to suppliers (SupplierPayment + GRN paid_amount)."""
    date_from, date_to_excl = _parse_date_range(default_days=0)
    search = (request.args.get('q') or '').strip()
    method_f = (request.args.get('method') or '').strip()

    sp_q = SupplierPayment.query.filter(
        SupplierPayment.is_void == False,
        SupplierPayment.date_posted >= date_from,
        SupplierPayment.date_posted < date_to_excl
    ).join(Supplier)

    if search:
        like = f'%{search}%'
        sp_q = sp_q.filter(or_(Supplier.name.ilike(like), SupplierPayment.note.ilike(like), SupplierPayment.account_name.ilike(like)))
    if method_f:
        sp_q = sp_q.filter(func.lower(SupplierPayment.method) == method_f.lower())

    supplier_payments = sp_q.order_by(SupplierPayment.date_posted.desc(), SupplierPayment.id.desc()).all()

    grn_q = GRN.query.filter(
        GRN.date_posted >= date_from,
        GRN.date_posted < date_to_excl,
        GRN.is_void == False,
        GRN.paid_amount > 0
    )
    if search:
        like = f'%{search}%'
        grn_q = grn_q.filter(or_(func.coalesce(GRN.supplier, '').ilike(like), func.coalesce(GRN.note, '').ilike(like), func.coalesce(GRN.account_name, '').ilike(like)))
    if method_f:
        grn_q = grn_q.filter(func.lower(func.coalesce(GRN.payment_type, '')) == method_f.lower())

    grns = grn_q.order_by(GRN.date_posted.desc(), GRN.id.desc()).all()

    items = []
    total_amount = 0.0

    for p in supplier_payments:
        supplier_obj = getattr(p, 'supplier', None)
        supplier_name = supplier_obj.name if supplier_obj else ''
        items.append({
            'date_posted': p.date_posted,
            'source': 'Supplier Payment',
            'party': supplier_name,
            'amount': float(p.amount or 0),
            'method': p.method or '',
            'account_name': p.account_name or '',
            'bill_no': p.manual_bill_no or p.auto_bill_no or '',
            'note': p.note or '',
            'link': (url_for('supplier_ledger', id=supplier_obj.id) if supplier_obj else None),
        })
        total_amount += float(p.amount or 0)

    for g in grns:
        supplier_obj = getattr(g, 'supplier_rel', None)
        supplier_name = (supplier_obj.name if supplier_obj else (g.supplier or ''))
        items.append({
            'date_posted': g.date_posted,
            'source': 'GRN Purchase Payment',
            'party': supplier_name,
            'amount': float(g.paid_amount or 0),
            'method': g.payment_type or '',
            'account_name': g.account_name or '',
            'bill_no': g.manual_bill_no or g.auto_bill_no or '',
            'note': g.note or '',
            'link': (url_for('supplier_ledger', id=supplier_obj.id) if supplier_obj else None),
        })
        total_amount += float(g.paid_amount or 0)

    items.sort(key=lambda x: (x.get('date_posted') or pk_now()), reverse=True)

    return render_template(
        'accounts/kpi_supplier_payments.html',
        items=items,
        total_amount=total_amount,
        total_count=len(items),
        date_from=date_from,
        date_to=date_to_excl - timedelta(days=1),
        search=search,
        method_f=method_f
    )


@accounts_bp.route('/kpi/expenditures')
@login_required
def kpi_expenditures():
    """KPI drill-down page: expenditures (cash drawer out entries)."""
    date_from, date_to_excl = _parse_date_range(default_days=0)
    search = (request.args.get('q') or '').strip()
    method_f = (request.args.get('method') or '').strip()

    q = FbmCashDrawerEntry.query.filter(
        FbmCashDrawerEntry.entry_type == 'out',
        FbmCashDrawerEntry.is_void == False,
        FbmCashDrawerEntry.date_posted >= date_from,
        FbmCashDrawerEntry.date_posted < date_to_excl
    )
    if search:
        like = f'%{search}%'
        q = q.filter(or_(func.coalesce(FbmCashDrawerEntry.category, '').ilike(like), func.coalesce(FbmCashDrawerEntry.note, '').ilike(like)))
    if method_f:
        q = q.filter(func.lower(FbmCashDrawerEntry.method) == method_f.lower())

    rows = q.order_by(FbmCashDrawerEntry.date_posted.desc(), FbmCashDrawerEntry.id.desc()).all()
    total_amount = float(sum(float(r.amount or 0) for r in rows))

    items = [{
        'date_posted': r.date_posted,
        'category': r.category or '',
        'amount': float(r.amount or 0),
        'method': r.method or '',
        'note': r.note or '',
    } for r in rows]

    return render_template(
        'accounts/kpi_expenditures.html',
        items=items,
        total_amount=total_amount,
        total_count=len(items),
        date_from=date_from,
        date_to=date_to_excl - timedelta(days=1),
        search=search,
        method_f=method_f
    )


@accounts_bp.route('/kpi/receipts')
@login_required
def kpi_receipts():
    """KPI drill-down page: receipts (Booking + DirectSale paid amounts)."""
    date_from, date_to_excl = _parse_date_range(default_days=0)
    search = (request.args.get('q') or '').strip()

    bookings_q = Booking.query.filter(
        Booking.date_posted >= date_from,
        Booking.date_posted < date_to_excl,
        Booking.is_void == False,
        Booking.paid_amount > 0
    )
    sales_q = DirectSale.query.filter(
        DirectSale.date_posted >= date_from,
        DirectSale.date_posted < date_to_excl,
        DirectSale.is_void == False,
        DirectSale.paid_amount > 0
    )
    if search:
        like = f'%{search}%'
        bookings_q = bookings_q.filter(or_(func.coalesce(Booking.client_name, '').ilike(like), func.coalesce(Booking.manual_bill_no, '').ilike(like), func.coalesce(Booking.auto_bill_no, '').ilike(like)))
        sales_q = sales_q.filter(or_(func.coalesce(DirectSale.client_name, '').ilike(like), func.coalesce(DirectSale.manual_bill_no, '').ilike(like), func.coalesce(DirectSale.auto_bill_no, '').ilike(like)))

    bookings = bookings_q.order_by(Booking.date_posted.desc(), Booking.id.desc()).all()
    sales = sales_q.order_by(DirectSale.date_posted.desc(), DirectSale.id.desc()).all()

    items = []
    total_amount = 0.0

    for b in bookings:
        items.append({
            'date_posted': b.date_posted,
            'source': 'Booking Receipt',
            'party': b.client_name or '',
            'amount': float(b.paid_amount or 0),
            'bill_no': getattr(b, 'manual_bill_no', None) or getattr(b, 'auto_bill_no', None) or '',
            'note': getattr(b, 'note', None) or '',
        })
        total_amount += float(b.paid_amount or 0)

    for s in sales:
        items.append({
            'date_posted': s.date_posted,
            'source': 'Direct Sale Receipt',
            'party': s.client_name or '',
            'amount': float(s.paid_amount or 0),
            'bill_no': s.manual_bill_no or s.auto_bill_no or '',
            'note': s.note or '',
        })
        total_amount += float(s.paid_amount or 0)

    items.sort(key=lambda x: (x.get('date_posted') or pk_now()), reverse=True)

    return render_template(
        'accounts/kpi_receipts.html',
        items=items,
        total_amount=total_amount,
        total_count=len(items),
        date_from=date_from,
        date_to=date_to_excl - timedelta(days=1),
        search=search
    )


@accounts_bp.route('/kpi/company_money')
@login_required
def kpi_company_money():
    """KPI drill-down page: company money available (active company accounts)."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    accounts = _company_accounts()
    total_money = float(sum(float(a.balance or 0) for a in accounts))
    items = [{
        'id': a.id,
        'name': a.name,
        'category': a.category,
        'account_type': a.account_type,
        'balance': float(a.balance or 0),
        'link': url_for('accounts.account_ledger', account_id=a.id)
    } for a in accounts]

    items.sort(key=lambda x: x.get('name') or '')

    return render_template('accounts/kpi_company_money.html', items=items, total_money=total_money, total_count=len(items))


@accounts_bp.route('/kpi/cash_money')
@login_required
def kpi_cash_money():
    """KPI drill-down page: total cash + total bank (2-step drill-down)."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    cash_accounts = Account.query.filter(
        func.coalesce(Account.is_active, True) == True,
        func.lower(func.trim(Account.category)) == 'cash'
    ).order_by(Account.name.asc()).all()
    
    bank_accounts = Account.query.filter(
        func.coalesce(Account.is_active, True) == True,
        func.lower(func.trim(Account.category)) == 'bank'
    ).order_by(Account.name.asc()).all()
    
    total_cash = float(sum(float(a.balance or 0) for a in cash_accounts))
    total_bank = float(sum(float(a.balance or 0) for a in bank_accounts))

    return render_template(
        'accounts/kpi_cash_money.html',
        total_cash=total_cash,
        total_bank=total_bank,
        cash_count=len(cash_accounts),
        bank_count=len(bank_accounts),
    )


@accounts_bp.route('/kpi/cash_accounts')
@login_required
def kpi_cash_accounts():
    """KPI drill-down page: list all cash accounts and their balances."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    cash_accounts = Account.query.filter(
        func.coalesce(Account.is_active, True) == True,
        func.lower(func.trim(Account.category)) == 'cash'
    ).order_by(Account.name.asc()).all()
    total_cash = float(sum(float(a.balance or 0) for a in cash_accounts))
    items = [{
        'id': a.id,
        'name': a.name,
        'category': a.category,
        'account_type': a.account_type,
        'balance': float(a.balance or 0),
        'link': url_for('accounts.account_ledger', account_id=a.id)
    } for a in cash_accounts]
    return render_template('accounts/kpi_cash_accounts.html', items=items, total_amount=total_cash, total_count=len(items))


@accounts_bp.route('/kpi/bank_accounts')
@login_required
def kpi_bank_accounts():
    """KPI drill-down page: list all bank accounts and their balances."""
    _ensure_default_account_categories()
    _backfill_legacy_account_groups()
    bank_accounts = Account.query.filter(
        func.coalesce(Account.is_active, True) == True,
        func.lower(func.trim(Account.category)) == 'bank'
    ).order_by(Account.name.asc()).all()
    total_bank = float(sum(float(a.balance or 0) for a in bank_accounts))
    items = [{
        'id': a.id,
        'name': a.name,
        'category': a.category,
        'account_type': a.account_type,
        'balance': float(a.balance or 0),
        'link': url_for('accounts.account_ledger', account_id=a.id)
    } for a in bank_accounts]
    return render_template('accounts/kpi_bank_accounts.html', items=items, total_amount=total_bank, total_count=len(items))
