from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from models import db, FBMRentalItem, FBMClient, FBMRental, Account, AccountTransaction

MODULE_CONFIG = {
    'name': 'FBM Rentals',
    'description': 'Manage rental inventory, clients, bookings, reporting, and payment posting.',
    'url_prefix': '/fbm_rentals',
    'enabled': True,
    'version': '1.0.0',
    'author': 'AMS System',
    'requires_login': True,
}

fbm_rentals_bp = Blueprint('fbm_rentals', __name__, template_folder='../templates')


def _now():
    return datetime.now()


def _parse_datetime(date_val, time_val=None):
    if date_val and 'T' in date_val:
        try:
            return datetime.strptime(date_val, '%Y-%m-%dT%H:%M')
        except Exception:
            pass
    if date_val and time_val:
        try:
            return datetime.strptime(f'{date_val} {time_val}', '%Y-%m-%d %H:%M')
        except Exception:
            pass
    if date_val:
        try:
            return datetime.strptime(date_val, '%Y-%m-%d')
        except Exception:
            pass
    return _now()


def _get_payment_accounts():
    return Account.query.filter(Account.is_active.is_(True), Account.category.in_(['cash', 'bank'])).order_by(Account.name.asc()).all()


def _get_client_total_due(client_id):
    """Calculate total pending/due amount for a client."""
    rentals = FBMRental.query.filter_by(client_id=client_id).all()
    return sum(r.balance_due for r in rentals)


@fbm_rentals_bp.route('/', methods=['GET'])
@login_required
def dashboard():
    active_items = FBMRentalItem.query.filter_by(is_void=False).order_by(FBMRentalItem.name.asc())
    active_clients = FBMClient.query.filter_by(is_active=True).order_by(FBMClient.full_name.asc())
    active_rentals = FBMRental.query.filter(FBMRental.status == 'active')
    returned_rentals = FBMRental.query.filter(FBMRental.status == 'returned')

    total_profit = sum((r.total_amount or 0) for r in returned_rentals)
    total_rentals = FBMRental.query.count()

    return render_template(
        'fbm_rentals/dashboard.html',
        inventory_count=active_items.count(),
        client_count=active_clients.count(),
        rental_count=FBMRental.query.count(),
        active_count=active_rentals.count(),
        reports_count=FBMRental.query.count(),
        profit_amount=total_profit,
    )


@fbm_rentals_bp.route('/inventory', methods=['GET'])
@login_required
def inventory():
    items = FBMRentalItem.query.filter_by(is_void=False).order_by(FBMRentalItem.name.asc()).all()
    return render_template('fbm_rentals/inventory.html', items=items)


@fbm_rentals_bp.route('/inventory/add', methods=['GET', 'POST'])
@login_required
def inventory_add():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        opening_qty = int(request.form.get('opening_qty', 0) or 0)
        rent_per_day = float(request.form.get('rent_per_day', 0) or 0)

        if not name:
            flash('Item name is required.', 'danger')
            return redirect(url_for('fbm_rentals.inventory_add'))

        item = FBMRentalItem(
            name=name,
            opening_qty=opening_qty,
            available_qty=opening_qty,
            rent_per_day=rent_per_day,
            is_void=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db.session.add(item)
        db.session.commit()
        flash('Rental inventory item created successfully.', 'success')
        return redirect(url_for('fbm_rentals.inventory'))

    return render_template('fbm_rentals/inventory_form.html', action='Add', item=None)


@fbm_rentals_bp.route('/inventory/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
def inventory_edit(item_id):
    item = FBMRentalItem.query.get_or_404(item_id)
    if request.method == 'POST':
        item.name = request.form.get('name', '').strip() or item.name
        opening_qty = int(request.form.get('opening_qty', item.opening_qty) or item.opening_qty)
        rent_per_day = float(request.form.get('rent_per_day', item.rent_per_day) or item.rent_per_day)

        if opening_qty < 0:
            flash('Opening quantity must be 0 or greater.', 'danger')
            return redirect(url_for('fbm_rentals.inventory_edit', item_id=item_id))

        delta = opening_qty - item.opening_qty
        item.opening_qty = opening_qty
        item.available_qty = max(0, item.available_qty + delta)
        item.rent_per_day = rent_per_day
        item.updated_at = _now()
        db.session.commit()
        flash('Inventory item updated successfully.', 'success')
        return redirect(url_for('fbm_rentals.inventory'))

    return render_template('fbm_rentals/inventory_form.html', action='Edit', item=item)


@fbm_rentals_bp.route('/inventory/void/<int:item_id>', methods=['POST'])
@login_required
def inventory_void(item_id):
    item = FBMRentalItem.query.get_or_404(item_id)
    item.is_void = True
    item.updated_at = _now()
    db.session.commit()
    flash('Item has been voided and removed from the active inventory list.', 'success')
    return redirect(url_for('fbm_rentals.inventory'))


@fbm_rentals_bp.route('/clients', methods=['GET', 'POST'])
@login_required
def clients():
    edit_id = request.args.get('edit_id', type=int)
    edit_client = FBMClient.query.get(edit_id) if edit_id else None
    if request.method == 'POST':
        client_id = request.form.get('client_id')
        full_name = request.form.get('full_name', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        identity_card = request.form.get('identity_card', '').strip()

        if not full_name:
            flash('Client name is required.', 'danger')
            return redirect(url_for('fbm_rentals.clients', edit_id=client_id) if client_id else url_for('fbm_rentals.clients'))

        if client_id:
            client = FBMClient.query.get_or_404(client_id)
            client.full_name = full_name
            client.address = address
            client.phone = phone
            client.identity_card = identity_card
            client.updated_at = _now()
            flash('Client updated successfully.', 'success')
        else:
            client = FBMClient(
                full_name=full_name,
                address=address,
                phone=phone,
                identity_card=identity_card,
                is_active=True,
                created_at=_now(),
                updated_at=_now(),
            )
            db.session.add(client)
            flash('Client added successfully.', 'success')

        db.session.commit()
        return redirect(url_for('fbm_rentals.clients'))

    clients = FBMClient.query.order_by(FBMClient.full_name.asc()).all()
    client_dues = {client.id: _get_client_total_due(client.id) for client in clients}
    return render_template('fbm_rentals/clients.html', clients=clients, edit_client=edit_client, client_dues=client_dues)


@fbm_rentals_bp.route('/clients/toggle/<int:client_id>', methods=['POST'])
@login_required
def clients_toggle_active(client_id):
    client = FBMClient.query.get_or_404(client_id)
    client.is_active = not client.is_active
    client.updated_at = _now()
    db.session.commit()
    flash(
        f'Client {"activated" if client.is_active else "suspended"} successfully.',
        'success'
    )
    return redirect(url_for('fbm_rentals.clients'))


@fbm_rentals_bp.route('/clients/<int:client_id>/ledger', methods=['GET'])
@login_required
def client_ledger(client_id):
    client = FBMClient.query.get_or_404(client_id)
    rentals = FBMRental.query.filter_by(client_id=client.id).order_by(FBMRental.start_datetime.desc()).all()
    total_due = sum(r.balance_due for r in rentals)
    total_estimated = sum(r.current_estimated_amount() for r in rentals if r.status == 'active')
    return render_template(
        'fbm_rentals/client_ledger.html',
        client=client,
        rentals=rentals,
        total_due=total_due,
        total_estimated=total_estimated,
    )


@fbm_rentals_bp.route('/clients/<int:client_id>/payment', methods=['GET', 'POST'])
@login_required
def client_payment(client_id):
    client = FBMClient.query.get_or_404(client_id)
    rentals = FBMRental.query.filter_by(client_id=client.id).all()
    total_due = sum(r.balance_due for r in rentals)

    if request.method == 'POST':
        try:
            payment_amount = float(request.form.get('payment_amount', 0) or 0)
            account_id = request.form.get('payment_account_id')
            notes = request.form.get('notes', '').strip()

            if payment_amount <= 0:
                flash('Payment amount must be greater than zero.', 'danger')
                return redirect(url_for('fbm_rentals.client_payment', client_id=client_id))

            if payment_amount > total_due:
                flash(f'Payment amount cannot exceed total due (Rs. {total_due:.2f}).', 'danger')
                return redirect(url_for('fbm_rentals.client_payment', client_id=client_id))

            # Allocate payment to rentals with balance due
            remaining_payment = payment_amount
            for rental in rentals:
                if remaining_payment <= 0:
                    break
                if rental.balance_due > 0:
                    allocation = min(remaining_payment, rental.balance_due)
                    rental.paid_amount = (rental.paid_amount or 0) + allocation
                    remaining_payment -= allocation

            # Post to account if selected
            if account_id:
                account = Account.query.get_or_404(account_id)
                txn = AccountTransaction(
                    from_account_id=account.id,
                    to_account_id=None,
                    amount=payment_amount,
                    transaction_type='FBM Rental Payment',
                    description=f'FBM rental payment from {client.full_name}',
                    date_posted=_now(),
                    is_void=False,
                    note=notes or f'Payment for client {client.full_name}',
                )
                db.session.add(txn)

            db.session.commit()
            flash(f'Payment of Rs. {payment_amount:.2f} recorded successfully.', 'success')
            return redirect(url_for('fbm_rentals.client_ledger', client_id=client_id))

        except ValueError:
            flash('Invalid payment amount.', 'danger')
            return redirect(url_for('fbm_rentals.client_payment', client_id=client_id))

    payment_accounts = _get_payment_accounts()
    return render_template(
        'fbm_rentals/client_payment.html',
        client=client,
        total_due=total_due,
        payment_accounts=payment_accounts,
    )


@fbm_rentals_bp.route('/rentals', methods=['GET', 'POST'])
@login_required
def rentals():
    edit_id = request.args.get('edit_id', type=int)
    edit_rental = FBMRental.query.get(edit_id) if edit_id else None
    clients = FBMClient.query.filter_by(is_active=True).order_by(FBMClient.full_name.asc()).all()
    items = FBMRentalItem.query.filter_by(is_void=False).order_by(FBMRentalItem.name.asc()).all()
    status_filter = request.args.get('status', '').strip().lower()
    client_filter = request.args.get('client_id', type=int)
    item_filter = request.args.get('item_id', type=int)
    date_from = request.args.get('from_date')
    date_to = request.args.get('to_date')

    query = FBMRental.query.order_by(FBMRental.start_datetime.desc())
    if status_filter:
        query = query.filter(FBMRental.status == status_filter)
    if client_filter:
        query = query.filter(FBMRental.client_id == client_filter)
    if item_filter:
        query = query.filter(FBMRental.item_id == item_filter)
    if date_from:
        try:
            dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(FBMRental.start_datetime >= dt)
        except Exception:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(FBMRental.start_datetime < dt)
        except Exception:
            pass

    rentals = query.all()
    return render_template(
        'fbm_rentals/rentals.html',
        clients=clients,
        items=items,
        rentals=rentals,
        edit_rental=edit_rental,
        filters={
            'from_date': date_from,
            'to_date': date_to,
            'status': status_filter,
            'client_id': client_filter,
            'item_id': item_filter,
        },
    )


@fbm_rentals_bp.route('/rentals/save', methods=['POST'])
@login_required
def rentals_save():
    rental_id = request.form.get('rental_id', type=int)
    client_id = request.form.get('client_id', type=int)
    item_id = request.form.get('item_id', type=int)
    qty = int(request.form.get('qty', 0) or 0)
    rent_per_unit = float(request.form.get('rent_per_unit', 0) or 0)
    start_date = request.form.get('start_date')
    start_time = request.form.get('start_time')

    if not client_id or not item_id or qty <= 0 or rent_per_unit < 0:
        flash('Please select client, item, quantity and rent amount.', 'danger')
        return redirect(url_for('fbm_rentals.rentals'))

    item = FBMRentalItem.query.get_or_404(item_id)
    client = FBMClient.query.get_or_404(client_id)
    start_datetime = _parse_datetime(start_date, start_time)
    total_amount = round(qty * rent_per_unit, 2)

    if rental_id:
        rental = FBMRental.query.get_or_404(rental_id)
        if rental.status != 'active':
            flash('Only active rentals can be updated.', 'warning')
            return redirect(url_for('fbm_rentals.rentals'))
        if rental.qty_returned > 0:
            flash('Cannot edit a rental after part of it has already been returned.', 'warning')
            return redirect(url_for('fbm_rentals.rentals', edit_id=rental_id))

        if rental.item_id != item_id:
            original_item = rental.item
            original_item.available_qty = max(0, original_item.available_qty + rental.qty)
            if item.available_qty < qty:
                flash('Not enough available quantity for the new item.', 'danger')
                return redirect(url_for('fbm_rentals.rentals', edit_id=rental_id))
            item.available_qty = max(0, item.available_qty - qty)
        else:
            qty_delta = qty - rental.qty
            if qty_delta > 0 and item.available_qty < qty_delta:
                flash('Not enough available quantity to increase this rental.', 'danger')
                return redirect(url_for('fbm_rentals.rentals', edit_id=rental_id))
            item.available_qty = max(0, item.available_qty - qty_delta)

        rental.client_id = client_id
        rental.item_id = item_id
        rental.qty = qty
        rental.rent_per_unit = rent_per_unit
        rental.start_datetime = start_datetime
        rental.total_amount = 0.0
        rental.updated_at = _now()
        flash('Rental updated successfully.', 'success')
    else:
        if item.available_qty < qty:
            flash('Not enough available quantity for this rental.', 'danger')
            return redirect(url_for('fbm_rentals.rentals'))

        item.available_qty = max(0, item.available_qty - qty)
        rental = FBMRental(
            client_id=client_id,
            item_id=item_id,
            qty=qty,
            rent_per_unit=rent_per_unit,
            total_amount=0.0,
            start_datetime=start_datetime,
            status='active',
            created_at=_now(),
            updated_at=_now(),
        )
        db.session.add(rental)
        flash('Rental saved successfully.', 'success')

    db.session.commit()
    return redirect(url_for('fbm_rentals.rentals'))


@fbm_rentals_bp.route('/rentals/void/<int:rental_id>', methods=['POST'])
@login_required
def rentals_void(rental_id):
    rental = FBMRental.query.get_or_404(rental_id)
    if rental.status != 'active':
        flash('Only active rentals can be voided.', 'warning')
        return redirect(url_for('fbm_rentals.rentals'))

    rental.status = 'void'
    rental.return_datetime = _now()
    item = rental.item
    item.available_qty = max(0, item.available_qty + rental.qty)
    rental.updated_at = _now()
    db.session.commit()
    flash('Rental voided and stock returned to inventory.', 'success')
    return redirect(url_for('fbm_rentals.rentals'))


@fbm_rentals_bp.route('/rentals/return/<int:rental_id>', methods=['GET', 'POST'])
@login_required
def rentals_return(rental_id):
    rental = FBMRental.query.get_or_404(rental_id)
    if rental.status != 'active':
        flash('Only active rentals can be returned.', 'warning')
        return redirect(url_for('fbm_rentals.rentals'))

    return_time = _now()
    remaining_qty = rental.remaining_qty
    days_used = rental.days_used(return_time)
    base_charge = rental.charge_for_qty(remaining_qty, return_time)

    qty_returned = remaining_qty
    discount_amount = 0.0
    paid_amount = 0.0
    charge_due = base_charge

    if request.method == 'POST':
        qty_returned = int(request.form.get('qty_returned', remaining_qty) or remaining_qty)
        discount_amount = float(request.form.get('discount_amount', 0) or 0)
        paid_amount = float(request.form.get('paid_amount', 0) or 0)
        account_id = request.form.get('payment_account_id', type=int)

        if qty_returned <= 0 or qty_returned > remaining_qty:
            flash('Please enter a valid return quantity.', 'danger')
            return redirect(url_for('fbm_rentals.rentals_return', rental_id=rental_id))

        if paid_amount < 0 or discount_amount < 0:
            flash('Paid amount and discount cannot be negative.', 'danger')
            return redirect(url_for('fbm_rentals.rentals_return', rental_id=rental_id))

        charge_due = rental.charge_for_qty(qty_returned, return_time)
        return_note = request.form.get('return_note', '').strip()
        rental.return_datetime = return_time
        rental.qty_returned = (rental.qty_returned or 0) + qty_returned
        rental.total_amount = round((rental.total_amount or 0) + charge_due, 2)
        rental.discount_amount = round((rental.discount_amount or 0) + discount_amount, 2)
        rental.paid_amount = round((rental.paid_amount or 0) + paid_amount, 2)
        rental.updated_at = _now()
        rental.item.available_qty = max(0, rental.item.available_qty + qty_returned)

        if return_note:
            rental.note = (rental.note or '').strip() + (f' | {return_note}' if rental.note else return_note)

        if rental.remaining_qty <= 0:
            rental.status = 'returned'

        if paid_amount > 0:
            if not account_id:
                flash('Select a payment account when recording payment.', 'danger')
                return redirect(url_for('fbm_rentals.rentals_return', rental_id=rental_id))
            account = Account.query.get_or_404(account_id)
            txn = AccountTransaction(
                from_account_id=account.id,
                to_account_id=None,
                amount=paid_amount,
                transaction_type='Rental Payment',
                description=f'FBM rental payment for rental #{rental.id}',
                date_posted=_now(),
                is_void=False,
                note=f'FBM rental return for client {rental.client.full_name}',
            )
            db.session.add(txn)
            rental.payment_account_id = account.id

        db.session.commit()
        flash('Rental return recorded successfully.', 'success')
        return redirect(url_for('fbm_rentals.rentals'))

    payment_accounts = _get_payment_accounts()
    return render_template(
        'fbm_rentals/rentals_return.html',
        rental=rental,
        payment_accounts=payment_accounts,
        now=return_time,
        days_used=days_used,
        qty_returned=qty_returned,
        discount_amount=discount_amount,
        paid_amount=paid_amount,
        charge_due=charge_due,
        remaining_qty=remaining_qty,
    )


@fbm_rentals_bp.route('/rentals/transfer/<int:rental_id>', methods=['GET', 'POST'])
@login_required
def rentals_transfer(rental_id):
    rental = FBMRental.query.get_or_404(rental_id)
    if rental.status != 'active':
        flash('Only active rentals can be transferred.', 'warning')
        return redirect(url_for('fbm_rentals.rentals'))

    clients = FBMClient.query.filter_by(is_active=True).order_by(FBMClient.full_name.asc()).all()
    transfer_at = _now()
    transfer_qty = rental.remaining_qty
    charge_due = rental.charge_for_qty(transfer_qty, transfer_at)

    if request.method == 'POST':
        new_client_id = request.form.get('new_client_id', type=int)
        if not new_client_id:
            flash('Please select a client to transfer this rental to.', 'danger')
            return redirect(url_for('fbm_rentals.rentals_transfer', rental_id=rental_id))
        if new_client_id == rental.client_id:
            flash('Please select a different client for the transfer.', 'warning')
            return redirect(url_for('fbm_rentals.rentals_transfer', rental_id=rental_id))

        rental.status = 'transferred'
        rental.return_datetime = transfer_at
        rental.qty_returned = (rental.qty_returned or 0) + transfer_qty
        rental.total_amount = round((rental.total_amount or 0) + charge_due, 2)
        rental.updated_at = _now()

        new_rental = FBMRental(
            client_id=new_client_id,
            item_id=rental.item_id,
            qty=transfer_qty,
            rent_per_unit=rental.rent_per_unit,
            total_amount=0.0,
            start_datetime=transfer_at,
            status='active',
            created_at=_now(),
            updated_at=_now(),
            note=f'Transferred from rental #{rental.id}',
        )
        db.session.add(new_rental)
        db.session.commit()
        flash('Rental transferred successfully and new rental started for the new client.', 'success')
        return redirect(url_for('fbm_rentals.rentals'))

    return render_template('fbm_rentals/rentals_transfer.html', rental=rental, clients=clients, transfer_at=transfer_at, charge_due=charge_due, transfer_qty=transfer_qty)


@fbm_rentals_bp.route('/reports', methods=['GET'])
@login_required
def reports():
    status_filter = request.args.get('status', '').strip().lower()
    client_filter = request.args.get('client_id', type=int)
    item_filter = request.args.get('item_id', type=int)
    date_from = request.args.get('from_date')
    date_to = request.args.get('to_date')

    query = FBMRental.query.order_by(FBMRental.start_datetime.desc())
    if status_filter:
        query = query.filter(FBMRental.status == status_filter)
    if client_filter:
        query = query.filter(FBMRental.client_id == client_filter)
    if item_filter:
        query = query.filter(FBMRental.item_id == item_filter)
    if date_from:
        try:
            dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(FBMRental.start_datetime >= dt)
        except Exception:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(FBMRental.start_datetime < dt)
        except Exception:
            pass

    rentals = query.all()
    total_revenue = sum((r.total_amount or 0) for r in rentals if r.status in ('returned', 'transferred'))
    collected_amount = sum((r.total_amount or 0) for r in rentals if r.status == 'returned')
    pending_amount = sum((r.current_estimated_amount() if r.status == 'active' else 0) for r in rentals)

    by_client = {}
    by_item = {}
    by_day = {}
    for rental in rentals:
        if rental.status in ('returned', 'transferred'):
            by_client.setdefault(rental.client.full_name, 0)
            by_client[rental.client.full_name] += rental.total_amount or 0
            by_item.setdefault(rental.item.name, 0)
            by_item[rental.item.name] += rental.total_amount or 0

        day_key = rental.start_datetime.strftime('%Y-%m-%d')
        by_day.setdefault(day_key, 0)
        by_day[day_key] += rental.total_amount or 0

    clients = FBMClient.query.filter_by(is_active=True).order_by(FBMClient.full_name.asc()).all()
    items = FBMRentalItem.query.filter_by(is_void=False).order_by(FBMRentalItem.name.asc()).all()
    return render_template(
        'fbm_rentals/reports.html',
        rentals=rentals,
        clients=clients,
        items=items,
        totals={
            'total_revenue': total_revenue,
            'collected_amount': collected_amount,
            'pending_amount': pending_amount,
            'total_rentals': len(rentals),
        },
        by_client=sorted(by_client.items(), key=lambda x: x[1], reverse=True),
        by_item=sorted(by_item.items(), key=lambda x: x[1], reverse=True),
        by_day=sorted(by_day.items()),
        filters={
            'from_date': date_from,
            'to_date': date_to,
            'status': status_filter,
            'client_id': client_filter,
            'item_id': item_filter,
        },
    )
