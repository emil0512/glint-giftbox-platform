import csv
import json
import logging
import secrets
import string
from decimal import Decimal
from decimal import InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.mail import get_connection, send_mail
from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_encode, urlsafe_base64_decode
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils.html import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import (
    UserProfile, EventOccasion, Category, GiftBox, BoxItem, CatalogAssignment,
    BoxConfiguration, BoxSelection, Order
)
from .forms import (
    RegisterForm, LoginForm, ProfileForm, CheckoutForm, AdminOrderUpdateForm,
    GiftBoxForm, BoxItemForm,
    ForgotPasswordRequestForm, VerifyOtpAndResetPasswordForm,
    validate_strict_password,
)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

logger = logging.getLogger(__name__)


def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

def log_admin_action(user, msg, request=None):
    LogEntry.objects.create(
        user=user,
        action_flag=CHANGE,
        object_repr=msg[:200],
        change_message=json.dumps({
            'action': msg,
            'ip': request.META.get('REMOTE_ADDR', '') if request else '',
        }),
    )
    logger.info(
        'Admin action user_id=%s action=%s ip=%s',
        user.pk,
        msg,
        request.META.get('REMOTE_ADDR', '') if request else '',
    )


def send_order_status_email(order, old_status, old_tracking_number=None):
    status_changed = old_status != order.order_status
    tracking_changed = old_tracking_number is not None and old_tracking_number != order.tracking_number
    if not status_changed and not tracking_changed:
        return
    try:
        send_mail(
            subject=(
                f'Order {order.order_number} update'
                if status_changed else f'Tracking details for order {order.order_number}'
            ),
            message=(
                f'Your order status is now {order.get_order_status_display()}.\n'
                f'Tracking number: {order.tracking_number or "Not assigned yet"}\n'
                f'Preferred delivery date: {order.delivery_date or "Not specified"}'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[order.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Unable to send order status email order=%s', order.order_number)


def send_order_confirmation_email(order):
    try:
        item_lines = []
        for configuration in order.configurations.prefetch_related('selections'):
            selected_items = ', '.join(
                f'{selection.item_name} x{selection.quantity}'
                for selection in configuration.selections.all()
            )
            item_lines.append(
                f'{configuration.quantity} x {configuration.box_name}: {selected_items}'
            )
        send_mail(
            subject=f'Order Confirmation - {order.order_number}',
            message=(
                f'Dear {order.full_name},\n\n'
                    f'Your order #{order.order_number} has been placed.\n'
                f'Order total: Rs.{order.grand_total}\n'
                f'Items:\n' + '\n'.join(item_lines) + '\n'
                f'Delivery address: {order.shipping_address}, {order.city}, '
                f'{order.state} - {order.postal_code}\n\n'
                'This checkout is in test mode; no payment was collected.\n'
                'Glint Gift Team'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[order.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception('Unable to send order confirmation email order=%s', order.order_number)
        return False


def restore_order_inventory(order):
    box_quantities = {}
    item_quantities = {}
    for configuration in order.configurations.prefetch_related('selections').all():
        if configuration.gift_box_id:
            box_quantities[configuration.gift_box_id] = (
                box_quantities.get(configuration.gift_box_id, 0) + configuration.quantity
            )
        for selection in configuration.selections.all():
            if selection.box_item_id:
                item_quantities[selection.box_item_id] = (
                    item_quantities.get(selection.box_item_id, 0)
                    + selection.quantity * configuration.quantity
                )

    for box_id, quantity in box_quantities.items():
        GiftBox.objects.filter(pk=box_id).update(stock=F('stock') + quantity)
    for item_id, quantity in item_quantities.items():
        BoxItem.objects.filter(pk=item_id).update(stock=F('stock') + quantity)


def apply_order_date_filters(orders, request):
    start_value = request.GET.get('start_date', '').strip()
    end_value = request.GET.get('end_date', '').strip()
    start_date = parse_date(start_value) if start_value else None
    end_date = parse_date(end_value) if end_value else None
    valid_range = True

    if start_value and not start_date:
        messages.error(request, 'Enter the start date in YYYY-MM-DD format.')
        valid_range = False
    if end_value and not end_date:
        messages.error(request, 'Enter the end date in YYYY-MM-DD format.')
        valid_range = False
    if start_date and end_date and start_date > end_date:
        messages.error(request, 'Start date must be on or before the end date.')
        valid_range = False

    if valid_range:
        if start_date:
            orders = orders.filter(created_at__date__gte=start_date)
        if end_date:
            orders = orders.filter(created_at__date__lte=end_date)
    return orders, start_value if valid_range else '', end_value if valid_range else ''


def get_or_set_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def items_available_for_box(gift_box):
    return BoxItem.objects.filter(is_active=True).filter(
        Q(catalog_assignments__gift_box=gift_box)
        | Q(catalog_assignments__event=gift_box.event)
    ).distinct()


def replace_cart_selections(cart_item, selected_items):
    available_items = (
        items_available_for_box(cart_item.gift_box)
        if cart_item.gift_box_id else BoxItem.objects.none()
    )
    available_items_by_id = available_items.in_bulk()
    quantities_by_item = {}
    for selected in selected_items:
        box_item = available_items_by_id.get(int(selected.get('id')))
        if box_item:
            quantity = max(1, int(selected.get('qty', 1)))
            quantities_by_item[box_item.pk] = quantities_by_item.get(box_item.pk, 0) + quantity

    selections = [
        BoxSelection(
                configuration=cart_item,
                box_item=available_items_by_id[item_id],
                item_name_at_order=available_items_by_id[item_id].name,
                unit_price_at_order=available_items_by_id[item_id].price,
                quantity=quantity,
        )
        for item_id, quantity in quantities_by_item.items()
    ]
    with transaction.atomic():
        BoxSelection.objects.filter(configuration=cart_item).delete()
        BoxSelection.objects.bulk_create(selections)


def migrate_guest_session_data(request, user):
    """Migrates guest cart items and saved drafts from session to authenticated user."""
    session_key = request.session.session_key
    if not session_key:
        return

    # Migrate Cart Items
    guest_configurations = BoxConfiguration.objects.filter(session_key=session_key)
    for configuration in guest_configurations:
        configuration.user = user
        configuration.session_key = None
        configuration.save(update_fields=['user', 'session_key'])

# ==============================================================================
# AUTHENTICATION VIEWS
# ==============================================================================

def activate_account(request, uidb64, token):
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user and not user.is_active and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=['is_active'])
        messages.success(request, 'Your account is activated. You can now sign in.')
    elif user and user.is_active:
        messages.info(request, 'This account has already been activated.')
    else:
        messages.error(request, 'This activation link is invalid or has expired.')
    return redirect('login')


def register(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            full_name = form.cleaned_data['full_name']
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']

            # Generate unique username from email
            base_username = email.split('@')[0]
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            # Split name into first and last
            parts = full_name.strip().split(' ', 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ''

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
            )
            UserProfile.objects.create(user=user)
            messages.success(request, 'Account created. You can now sign in.')
            return redirect('login')
    else:
        form = RegisterForm()

    return render(request, 'register.html', {
        'form': form,
        'next': request.GET.get('next') or request.POST.get('next', ''),
    })


def user_login(request):
    if request.user.is_authenticated:
        return redirect('home')

    next_url = request.GET.get('next') or request.POST.get('next', '')
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username_or_email = form.cleaned_data['username_or_email'].strip()
            password = form.cleaned_data['password']
            remember_me = form.cleaned_data.get('remember_me', False)

            # Resolve user by username or email
            target_user = None
            if '@' in username_or_email:
                target_user = User.objects.filter(email__iexact=username_or_email).first()
            else:
                target_user = User.objects.filter(username__iexact=username_or_email).first()

            if target_user:
                if not target_user.is_active:
                    messages.error(
                        request,
                        'This account is not activated. Use Forgot Password to verify your email and activate it.',
                    )
                    return render(request, 'login.html', {'form': form, 'next': next_url})

                cache_key = f"login_attempts_{target_user.username}"
                attempts = cache.get(cache_key, 0)
                
                if attempts >= 5:
                    messages.error(request, '🔒 Account locked! 5 consecutive failed login attempts. Please try again after 15 minutes or click "Forgot Password".')
                    return render(request, 'login.html', {'form': form, 'next': next_url})

                authenticated_user = authenticate(request, username=target_user.username, password=password)
                if authenticated_user:
                    cache.delete(cache_key)
                    login(request, authenticated_user)

                    # Remember Me handling
                    if remember_me:
                        request.session.set_expiry(60 * 60 * 24 * 30)  # 30 days
                    else:
                        request.session.set_expiry(settings.SESSION_COOKIE_AGE)  # 30 mins

                    migrate_guest_session_data(request, authenticated_user)

                    messages.success(request, f'🌸 Welcome back, {authenticated_user.get_full_name() or authenticated_user.username}!')

                    next_url = request.GET.get('next') or request.POST.get('next')
                    if next_url and url_has_allowed_host_and_scheme(
                        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
                    ):
                        return redirect(next_url)
                    if authenticated_user.is_staff:
                        return redirect('admin_dashboard')
                    return redirect('profile')
                else:
                    attempts += 1
                    cache.set(cache_key, attempts, 900)  # Lockout for 15 mins
                    if attempts >= 5:
                        messages.error(request, '🔒 Account locked! 5 consecutive failed login attempts. Please try again after 15 minutes or click "Forgot Password".')
                    else:
                        messages.error(request, f'❌ Invalid credentials. {5 - attempts} attempt(s) remaining.')
            else:
                messages.error(request, '❌ Invalid email/username or password.')
    else:
        form = LoginForm()

    return render(request, 'login.html', {'form': form, 'next': next_url})


@require_POST
def user_logout(request):
    logout(request)
    messages.success(request, '👋 You have been logged out securely. See you soon!')
    return redirect('home')


def forgot_password(request):
    """
    Step 1: User provides registered email.
    Generates and emails a 6-digit OTP, stores it in session for 10 minutes.
    """
    form = ForgotPasswordRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        user = User.objects.filter(email__iexact=email).first()

        if not user:
            messages.success(
                request,
                'If an account exists for that email, password reset instructions will be sent.',
            )
            return redirect('forgot_password')

        if isinstance(get_connection(), ConsoleEmailBackend):
            logger.warning('Password reset requested but email delivery is not configured.')
            messages.error(
                request,
                'Email delivery is not configured yet. Please contact support to reset your password.',
            )
            return redirect('forgot_password')

        otp = ''.join(secrets.choice(string.digits) for _ in range(6))
        request.session['password_reset_otp'] = otp
        request.session['password_reset_email'] = email
        request.session['password_reset_otp_at'] = timezone.now().isoformat()
        request.session['password_reset_attempts'] = 0

        try:
            send_mail(
                subject='Your Glint Password Reset OTP',
                message=(
                    f'Hello {user.first_name or user.username},\n\n'
                    f'Your one-time password (OTP) to reset your Glint account password is:\n\n'
                    f'    {otp}\n\n'
                    f'This OTP is valid for 10 minutes. Do not share it with anyone.\n\n'
                    f'If you did not request a password reset, please ignore this email.\n\n'
                    f'Warm regards,\nGlint Support Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            logger.exception('Unable to send password reset email user_id=%s', user.pk)
            for key in (
                'password_reset_otp', 'password_reset_email',
                'password_reset_otp_at', 'password_reset_attempts',
            ):
                request.session.pop(key, None)
            messages.error(request, 'We could not send the reset email. Please try again later.')
            return redirect('forgot_password')

        messages.success(
            request,
            'If an account exists for that email, password reset instructions will be sent.'
        )
        return redirect('reset_password_otp')

    return render(request, 'forgot_password.html', {'form': form})


def reset_password_otp(request):
    """
    Step 2: User enters OTP and new password.
    Validates OTP from session, enforces strict password rules, resets password.
    """
    # Guard: must have a pending OTP in session
    if not request.session.get('password_reset_otp'):
        messages.error(request, 'No active password reset request found. Please start again.')
        return redirect('forgot_password')

    form = VerifyOtpAndResetPasswordForm(request.POST or None)
    masked_email = request.session.get('password_reset_email', '')

    if request.method == 'POST' and form.is_valid():
        submitted_otp = form.cleaned_data['otp']
        new_password = form.cleaned_data['new_password']

        # Check OTP expiry (10 minutes)
        otp_at_str = request.session.get('password_reset_otp_at')
        if otp_at_str:
            from django.utils.dateparse import parse_datetime
            otp_at = parse_datetime(otp_at_str)
            if otp_at and (timezone.now() - otp_at).total_seconds() > 600:
                # Clear stale session keys
                for k in (
                    'password_reset_otp', 'password_reset_email',
                    'password_reset_otp_at', 'password_reset_attempts',
                ):
                    request.session.pop(k, None)
                messages.error(request, 'Your OTP has expired (valid for 10 minutes). Please request a new one.')
                return redirect('forgot_password')

        stored_otp = request.session.get('password_reset_otp')
        if submitted_otp != stored_otp:
            attempts = request.session.get('password_reset_attempts', 0) + 1
            if attempts >= 5:
                for key in (
                    'password_reset_otp', 'password_reset_email',
                    'password_reset_otp_at', 'password_reset_attempts',
                ):
                    request.session.pop(key, None)
                messages.error(request, 'Too many incorrect OTP attempts. Please request a new code.')
                return redirect('forgot_password')
            request.session['password_reset_attempts'] = attempts
            form.add_error('otp', 'The OTP you entered is incorrect. Please try again.')
            return render(request, 'reset_password.html', {'form': form, 'masked_email': masked_email})

        # OTP matches — reset password
        email = request.session.get('password_reset_email', '')
        user = User.objects.filter(email__iexact=email).first()
        if user:
            user.set_password(new_password)
            user.is_active = True
            user.save(update_fields=['password', 'is_active'])
            cache.delete(f"login_attempts_{user.username}")
        else:
            for key in (
                'password_reset_otp', 'password_reset_email',
                'password_reset_otp_at', 'password_reset_attempts',
            ):
                request.session.pop(key, None)
            messages.error(request, 'This account is no longer available. Please request a new reset link.')
            return redirect('forgot_password')

        # Clear all reset session keys
        for k in (
            'password_reset_otp', 'password_reset_email',
            'password_reset_otp_at', 'password_reset_attempts',
        ):
            request.session.pop(k, None)

        messages.success(request, 'Password reset successfully! Please log in with your new password.')
        return redirect('login')

    return render(request, 'reset_password.html', {'form': form, 'masked_email': masked_email})


def terms_of_service(request):
    return render(request, 'terms_of_service.html')


def privacy_policy(request):
    return render(request, 'privacy_policy.html')


# ==============================================================================
# CATALOG & EVENT-BASED BROWSING VIEWS
# ==============================================================================

def home(request):
    occasions = EventOccasion.objects.filter(is_active=True).order_by('name')
    featured_boxes = GiftBox.objects.filter(is_active=True).select_related('event')[:8]
    categories = Category.objects.filter(is_active=True)
    return render(request, 'home.html', {
        'occasions': occasions,
        'featured_boxes': featured_boxes,
        'categories': categories,
    })


def gift_boxes(request):
    boxes = GiftBox.objects.filter(is_active=True).select_related('event')
    occasions = EventOccasion.objects.filter(is_active=True).order_by('name')
    categories = Category.objects.filter(is_active=True)

    category_name = request.GET.get('category')
    sort_by = request.GET.get('sort', 'name')
    query = request.GET.get('query', '').strip()

    if category_name:
        boxes = boxes.filter(available_items__catalog_assignments__category__name=category_name).distinct()

    if query:
        boxes = boxes.filter(
            Q(name__icontains=query) |
            Q(event__name__icontains=query) |
            Q(available_items__name__icontains=query)
        ).distinct()

    if sort_by == 'price_low':
        boxes = boxes.order_by('base_price')
    elif sort_by == 'price_high':
        boxes = boxes.order_by('-base_price')
    elif sort_by == 'name':
        boxes = boxes.order_by('name')
    else:
        boxes = boxes.order_by('name')

    categories = Category.objects.filter(is_active=True)

    return render(request, 'gift_boxes.html', {
        'gift_boxes': boxes,
        'occasions': occasions,
        'categories': categories,
        'selected_category': category_name,
        'selected_sort': sort_by,
        'query': query,
    })


def gift_boxes_by_event(request, event_id):
    occasion = get_object_or_404(EventOccasion, pk=event_id, is_active=True)
    boxes = GiftBox.objects.filter(is_active=True).filter(
        Q(event=occasion) |
        Q(available_items__catalog_assignments__event=occasion)
    ).select_related('event').distinct()
    occasions = EventOccasion.objects.filter(is_active=True).order_by('name')
    categories = Category.objects.filter(is_active=True)
    recommended_items = BoxItem.objects.filter(
        catalog_assignments__event=occasion,
        is_active=True,
    ).distinct()

    category_name = request.GET.get('category')
    query = request.GET.get('query', '').strip()
    sort_by = request.GET.get('sort', 'name')
    if category_name:
        recommended_items = recommended_items.filter(
            catalog_assignments__category__name=category_name,
        ).distinct()
        recommended_item_ids = BoxItem.objects.filter(
            catalog_assignments__event=occasion,
        ).filter(
            catalog_assignments__category__name=category_name,
        ).values('pk')
        boxes = boxes.filter(
            Q(available_items__catalog_assignments__category__name=category_name)
            | Q(available_items__pk__in=recommended_item_ids)
        ).distinct()

    if query:
        boxes = boxes.filter(
            Q(name__icontains=query) |
            Q(available_items__name__icontains=query)
        ).distinct()

    if sort_by == 'price_low':
        boxes = boxes.order_by('base_price')
    elif sort_by == 'price_high':
        boxes = boxes.order_by('-base_price')
    elif sort_by == 'name':
        boxes = boxes.order_by('name')
    else:
        sort_by = 'name'
        boxes = boxes.order_by('name')

    return render(request, 'gift_boxes.html', {
        'gift_boxes': boxes,
        'current_occasion': occasion,
        'occasions': occasions,
        'categories': categories,
        'selected_category': category_name,
        'selected_sort': sort_by,
        'query': query,
        'event_recommended_items': recommended_items,
    })


def search_gift_boxes(request):
    return gift_boxes(request)


def gift_box_detail(request, box_id):
    box = get_object_or_404(GiftBox, pk=box_id, is_active=True)
    default_items = box.default_items.filter(is_active=True)
    available_items = items_available_for_box(box)
    related_boxes = GiftBox.objects.filter(event=box.event, is_active=True).exclude(id=box.id)[:3]

    default_total = box.get_default_total()

    return render(request, 'gift_box_detail.html', {
        'box': box,
        'default_items': default_items,
        'available_items': available_items,
        'default_total': default_total,
        'related_boxes': related_boxes,
    })


# ==============================================================================
# 3-STEP CUSTOMIZER BUILDER & DRAFT MANAGEMENT
# ==============================================================================

def customizer_builder(request, box_id):
    """
    Interactive 3-Stage Customizer Wizard:
    Step 1: Item Selection & Quantity Adjustments (with real-time price preview)
    Step 2: Personalization (Recipient name mandatory, custom quote, message, instructions)
    Step 3: Comprehensive Review & Price Breakdown
    """
    box = get_object_or_404(GiftBox, pk=box_id, is_active=True)
    available_items = list(items_available_for_box(box))
    available_items_by_id = {item.pk: item for item in available_items}
    default_item_ids = list(box.default_items.filter(is_active=True).values_list('id', flat=True))

    # Pre-population states (from draft, cart edit, or reorder)
    draft_id = request.GET.get('draft_id')
    cart_item_id = request.GET.get('edit_cart')
    reorder_item_id = request.GET.get('reorder')

    prefill_data = {
        'selected_item_map': {item_id: 1 for item_id in default_item_ids},
        'personalization_name': '',
        'personalization_message': '',
        'special_instructions': '',
        'draft_id': draft_id or '',
        'cart_item_id': cart_item_id or '',
    }
    unavailable_selections = []

    if draft_id:
        if request.user.is_authenticated:
            draft = BoxConfiguration.objects.filter(
                id=draft_id, user=request.user, status=BoxConfiguration.DRAFT,
            ).first()
        else:
            session_key = get_or_set_session_key(request)
            draft = BoxConfiguration.objects.filter(
                id=draft_id, session_key=session_key, status=BoxConfiguration.DRAFT,
            ).first()

        if draft:
            item_map = {}
            for selection in draft.selections.all():
                item = available_items_by_id.get(selection.box_item_id)
                if item:
                    item_map[selection.box_item_id] = selection.quantity
                    if item.stock < selection.quantity:
                        unavailable_selections.append(selection.item_name)
                else:
                    unavailable_selections.append(selection.item_name)
            prefill_data['selected_item_map'] = item_map
            prefill_data['personalization_name'] = draft.personalization_name
            prefill_data['personalization_message'] = draft.personalization_message
            prefill_data['special_instructions'] = draft.special_instructions
            messages.info(request, f"✨ Loaded saved draft #{draft.id} for '{draft.gift_box.name}'")

    elif cart_item_id:
        if request.user.is_authenticated:
            cart_item = BoxConfiguration.objects.filter(
                id=cart_item_id, user=request.user, status=BoxConfiguration.CART,
            ).first()
        else:
            session_key = get_or_set_session_key(request)
            cart_item = BoxConfiguration.objects.filter(
                id=cart_item_id, session_key=session_key, status=BoxConfiguration.CART,
            ).first()

        if cart_item:
            item_map = {}
            for selection in cart_item.selections.all():
                item = available_items_by_id.get(selection.box_item_id)
                if item:
                    item_map[selection.box_item_id] = selection.quantity
                    if item.stock < selection.quantity:
                        unavailable_selections.append(selection.item_name)
                else:
                    unavailable_selections.append(selection.item_name)
            prefill_data['selected_item_map'] = item_map
            prefill_data['personalization_name'] = cart_item.personalization_name
            prefill_data['personalization_message'] = cart_item.personalization_message
            prefill_data['special_instructions'] = cart_item.special_instructions

    elif reorder_item_id and request.user.is_authenticated:
        order_item = BoxConfiguration.objects.filter(
            id=reorder_item_id, order__user=request.user, status=BoxConfiguration.ORDERED,
        ).first()
        if order_item:
            item_map = {}
            for selection in order_item.selections.all():
                item = available_items_by_id.get(selection.box_item_id)
                if item:
                    item_map[selection.box_item_id] = selection.quantity
                    if item.stock < selection.quantity:
                        unavailable_selections.append(selection.item_name)
                else:
                    unavailable_selections.append(selection.item_name)
            prefill_data['selected_item_map'] = item_map
            prefill_data['personalization_name'] = order_item.personalization_name
            prefill_data['personalization_message'] = order_item.personalization_message
            prefill_data['special_instructions'] = order_item.special_instructions
            messages.info(request, f"🎁 Re-created configuration from past order #{order_item.order.order_number}")

    if unavailable_selections:
        messages.warning(
            request,
            'Some saved selections are unavailable or exceed current stock; review this box before saving: '
            + ', '.join(sorted(set(unavailable_selections))),
        )

    # Prepare serialized JSON for frontend JS dynamic recalculator
    items_json = []
    for it in available_items:
        items_json.append({
            'id': it.id,
            'name': it.name,
            'category': it.category_name,
            'price': float(it.price),
            'stock': it.stock,
            'is_default': it.id in default_item_ids,
        })

    return render(request, 'builder.html', {
        'box': box,
        'available_items': available_items,
        'items_json_str': json.dumps(items_json),
        'prefill_json_str': json.dumps(prefill_data),
        'prefill_data': prefill_data,
    })


@require_POST
def save_draft_api(request):
    """Saves or updates incomplete gift box draft via AJAX / POST."""
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return JsonResponse({'status': 'error', 'message': 'Draft data must be a JSON object.'}, status=400)
        box_id = data.get('box_id')
        draft_id = data.get('draft_id')
        draft_name = data.get('draft_name', '').strip()
        selected_items_data = data.get('items', [])
        if not isinstance(selected_items_data, list) or any(
            not isinstance(item, dict) for item in selected_items_data
        ):
            return JsonResponse({'status': 'error', 'message': 'Draft items must be a list of item objects.'}, status=400)
        personalization_name = data.get('personalization_name', '').strip()
        personalization_message = data.get('personalization_message', '').strip()
        special_instructions = data.get('special_instructions', '').strip()

        gift_box = get_object_or_404(GiftBox, id=box_id, is_active=True)
        session_key = get_or_set_session_key(request)

        if draft_id:
            if request.user.is_authenticated:
                draft = BoxConfiguration.objects.filter(
                    id=draft_id, user=request.user, status=BoxConfiguration.DRAFT,
                ).first()
            else:
                draft = BoxConfiguration.objects.filter(
                    id=draft_id, session_key=session_key, status=BoxConfiguration.DRAFT,
                ).first()
        else:
            draft = BoxConfiguration(
                user=request.user if request.user.is_authenticated else None,
                session_key=session_key if not request.user.is_authenticated else None,
                gift_box=gift_box,
                status=BoxConfiguration.DRAFT,
            )

        if not draft:
            draft = BoxConfiguration(
                user=request.user if request.user.is_authenticated else None,
                session_key=session_key if not request.user.is_authenticated else None,
                gift_box=gift_box,
                status=BoxConfiguration.DRAFT,
            )

        with transaction.atomic():
            draft.gift_box = gift_box
            draft.status = BoxConfiguration.DRAFT
            draft.draft_name = draft_name
            draft.personalization_name = personalization_name
            draft.personalization_message = personalization_message
            draft.special_instructions = special_instructions
            draft.save()
            replace_cart_selections(draft, selected_items_data)

        return JsonResponse({
            'status': 'success',
            'draft_id': draft.id,
            'message': 'Draft saved successfully! You can resume it anytime from your profile.',
            'is_guest': not request.user.is_authenticated
        })
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Draft data must contain valid JSON.'}, status=400)
    except Http404:
        return JsonResponse({'status': 'error', 'message': 'The selected gift box or draft was not found.'}, status=404)
    except (AttributeError, TypeError, ValueError) as error:
        return JsonResponse({'status': 'error', 'message': f'Invalid draft data: {error}'}, status=400)
    except Exception:
        logger.exception('Unable to save gift-box draft user_id=%s', request.user.pk if request.user.is_authenticated else None)
        return JsonResponse(
            {'status': 'error', 'message': 'Unable to save this draft right now. Please try again.'},
            status=500,
        )


@login_required
@require_POST
def delete_draft(request, draft_id):
    draft = get_object_or_404(
        BoxConfiguration, id=draft_id, user=request.user, status=BoxConfiguration.DRAFT,
    )
    draft.delete()
    messages.success(request, '🗑️ Saved draft deleted successfully.')
    return redirect('profile')


@login_required
@require_POST
def rename_draft(request, draft_id):
    draft = get_object_or_404(
        BoxConfiguration, id=draft_id, user=request.user, status=BoxConfiguration.DRAFT,
    )
    if request.method == 'POST':
        draft_name = request.POST.get('draft_name', '').strip()
        if draft_name:
            draft.draft_name = draft_name[:150]
            draft.save(update_fields=['draft_name', 'updated_at'])
            messages.success(request, 'Draft name updated.')
        else:
            messages.error(request, 'Draft name cannot be empty.')
    return redirect('profile')





# ==============================================================================
# SHOPPING CART VIEWS
# ==============================================================================

def add_to_cart_from_builder(request, box_id):
    """Adds customized box to cart or updates an existing cart item."""
    if request.method != 'POST':
        return redirect('gift_boxes')

    box = get_object_or_404(GiftBox, pk=box_id, is_active=True)
    session_key = get_or_set_session_key(request)

    cart_item_id = request.POST.get('cart_item_id')
    draft_id = request.POST.get('draft_id')
    personalization_name = request.POST.get('personalization_name', '').strip()
    personalization_message = request.POST.get('personalization_message', '').strip()
    special_instructions = request.POST.get('personalization_instructions', '').strip()

    # Recipient name is mandatory per SRS Section 3
    if not personalization_name:
        messages.error(request, '❌ Recipient Name is required to personalize your gift box.')
        return redirect('customizer_builder', box_id=box.pk)

    # Parse selected items and calculate price server-side for security
    raw_items_json = request.POST.get('selected_items_json', '[]')
    try:
        selected_items_list = json.loads(raw_items_json)
    except json.JSONDecodeError:
        messages.error(request, 'The selected products could not be read. Please review your gift box.')
        return redirect('customizer_builder', box_id=box.pk)

    if not isinstance(selected_items_list, list) or not selected_items_list:
        messages.error(request, '❌ Your gift box must contain at least one item.')
        return redirect('customizer_builder', box_id=box.pk)

    verified_items = []
    available_products = items_available_for_box(box).in_bulk()

    for it in selected_items_list:
        if not isinstance(it, dict):
            messages.error(request, 'One of the selected products is invalid. Please review your gift box.')
            return redirect('customizer_builder', box_id=box.pk)
        try:
            item_id = int(it.get('id'))
        except (TypeError, ValueError):
            messages.error(request, 'One of the selected products is invalid. Please review your gift box.')
            return redirect('customizer_builder', box_id=box.pk)
        item_obj = available_products.get(item_id)
        if not item_obj:
            messages.error(request, 'One of the selected products is no longer available in this box.')
            return redirect('customizer_builder', box_id=box.pk)
        try:
            qty = int(it.get('qty', 1))
        except (TypeError, ValueError):
            messages.error(request, f'Enter a valid quantity for {item_obj.name}.')
            return redirect('customizer_builder', box_id=box.pk)
        if qty < 1 or qty > item_obj.stock:
            messages.error(request, f'Only {item_obj.stock} of {item_obj.name} are currently available.')
            return redirect('customizer_builder', box_id=box.pk)
        verified_items.append({
            'id': item_obj.id,
            'name': item_obj.name,
            'category': item_obj.category_name,
            'price': float(item_obj.price),
            'qty': qty,
        })

    configuration_id = cart_item_id or draft_id
    if configuration_id:
        if request.user.is_authenticated:
            cart_item = BoxConfiguration.objects.filter(
                id=configuration_id, user=request.user,
                status__in=[BoxConfiguration.DRAFT, BoxConfiguration.CART],
            ).first()
        else:
            cart_item = BoxConfiguration.objects.filter(
                id=configuration_id, session_key=session_key,
                status__in=[BoxConfiguration.DRAFT, BoxConfiguration.CART],
            ).first()
        if cart_item:
            cart_item.gift_box = box
            cart_item.status = BoxConfiguration.CART
            cart_item.personalization_name = personalization_name
            cart_item.personalization_message = personalization_message
            cart_item.special_instructions = special_instructions
            cart_item.save()
            replace_cart_selections(cart_item, verified_items)
            messages.success(request, f'✨ Updated {box.name} in your cart!')
            return redirect('cart')

    # Create new cart item
    cart_item = BoxConfiguration(
        user=request.user if request.user.is_authenticated else None,
        session_key=session_key if not request.user.is_authenticated else None,
        gift_box=box,
        status=BoxConfiguration.CART,
        personalization_name=personalization_name,
        personalization_message=personalization_message,
        special_instructions=special_instructions,
        quantity=1
    )
    cart_item.save()
    replace_cart_selections(cart_item, verified_items)

    messages.success(request, f'🎁 {box.name} added to your cart!')
    return redirect('cart')


def cart_view(request):
    session_key = get_or_set_session_key(request)
    if request.user.is_authenticated:
        cart_items = BoxConfiguration.objects.filter(
            user=request.user, status=BoxConfiguration.CART,
        ).select_related('gift_box', 'gift_box__event').prefetch_related('selections')
    else:
        cart_items = BoxConfiguration.objects.filter(
            session_key=session_key, status=BoxConfiguration.CART,
        ).select_related('gift_box', 'gift_box__event').prefetch_related('selections')

    subtotal = sum(item.get_total_price() for item in cart_items)
    tax_amount = round(subtotal * settings.TAX_RATE, 2)
    shipping_amount = Decimal('0.00') if subtotal >= settings.FREE_SHIPPING_THRESHOLD or subtotal == 0 else settings.FLAT_SHIPPING_FEE
    grand_total = subtotal + tax_amount + shipping_amount

    return render(request, 'cart.html', {
        'cart_items': cart_items,
        'subtotal': subtotal,
        'tax_amount': tax_amount,
        'shipping_amount': shipping_amount,
        'grand_total': grand_total,
        'free_shipping_threshold': settings.FREE_SHIPPING_THRESHOLD,
    })


@require_POST
def update_cart_quantity(request, item_id, action):
    session_key = get_or_set_session_key(request)
    if request.user.is_authenticated:
        item = get_object_or_404(
            BoxConfiguration, id=item_id, user=request.user, status=BoxConfiguration.CART,
        )
    else:
        item = get_object_or_404(
            BoxConfiguration, id=item_id, session_key=session_key, status=BoxConfiguration.CART,
        )

    if action == 'increase':
        next_quantity = item.quantity + 1
        if not item.gift_box or item.gift_box.stock < next_quantity:
            messages.error(request, 'There is not enough gift box stock to increase this quantity.')
        else:
            selections = item.selections.select_related('box_item')
            unavailable = [
                selection.item_name
                for selection in selections
                if not selection.box_item_id
                or selection.box_item.stock < selection.quantity * next_quantity
            ]
            if unavailable:
                messages.error(
                    request,
                    'Not enough stock to increase the quantity for: ' + ', '.join(unavailable),
                )
            else:
                item.quantity = next_quantity
                item.save(update_fields=['quantity', 'updated_at'])
    elif action == 'decrease':
        if item.quantity > 1:
            item.quantity -= 1
            item.save()
        else:
            item.delete()
            messages.info(request, 'Item removed from cart.')

    elif action != 'decrease':
        messages.error(request, 'Choose a valid cart quantity action.')
    return redirect('cart')


@require_POST
def remove_from_cart(request, item_id):
    session_key = get_or_set_session_key(request)
    if request.user.is_authenticated:
        item = get_object_or_404(
            BoxConfiguration, id=item_id, user=request.user, status=BoxConfiguration.CART,
        )
    else:
        item = get_object_or_404(
            BoxConfiguration, id=item_id, session_key=session_key, status=BoxConfiguration.CART,
        )

    item.delete()
    messages.success(request, '🗑️ Gift box removed from cart.')
    return redirect('cart')


# ==============================================================================
# CHECKOUT & PAYMENT PROCESSING VIEWS
# ==============================================================================

@login_required
def checkout(request):
    cart_items = BoxConfiguration.objects.filter(
        user=request.user, status=BoxConfiguration.CART,
    ).select_related('gift_box').prefetch_related('selections')
    if not cart_items.exists():
        messages.warning(request, '🛒 Your cart is currently empty! Add some gift boxes before checking out.')
        return redirect('gift_boxes')

    subtotal = sum(item.get_total_price() for item in cart_items)
    tax_amount = round(subtotal * settings.TAX_RATE, 2)
    shipping_amount = Decimal('0.00') if subtotal >= settings.FREE_SHIPPING_THRESHOLD else settings.FLAT_SHIPPING_FEE
    grand_total = subtotal + tax_amount + shipping_amount

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    initial_data = {
        'full_name': request.user.get_full_name() or request.user.username,
        'email': request.user.email,
        'phone': profile.phone,
        'shipping_address': profile.address_line1,
        'city': profile.city,
        'state': profile.state,
        'postal_code': profile.postal_code,
        'payment_method': 'card',
    }

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            checkout_data = {
                'full_name': form.cleaned_data['full_name'],
                'email': form.cleaned_data['email'],
                'phone': form.cleaned_data['phone'],
                'shipping_address': form.cleaned_data['shipping_address'],
                'city': form.cleaned_data['city'],
                'state': form.cleaned_data['state'],
                'postal_code': form.cleaned_data['postal_code'],
                'payment_method': form.cleaned_data['payment_method'],
                'delivery_date': (
                    form.cleaned_data['delivery_date'].isoformat()
                    if form.cleaned_data['delivery_date'] else ''
                ),
            }
            return _complete_checkout(request, checkout_data)
    else:
        form = CheckoutForm(initial=initial_data)

    return render(request, 'checkout.html', {
        'form': form,
        'cart_items': cart_items,
        'subtotal': subtotal,
        'tax_amount': tax_amount,
        'shipping_amount': shipping_amount,
        'grand_total': grand_total,
    })


def _complete_checkout(request, checkout_data):
    payment_method = checkout_data.get('payment_method', 'card')
    try:
        with transaction.atomic():
                locked_items = list(
                    BoxConfiguration.objects.select_for_update()
                    .filter(user=request.user, status=BoxConfiguration.CART)
                    .order_by('pk')
                )
                if not locked_items:
                    raise ValidationError('Your cart is empty.')

                box_ids = {configuration.gift_box_id for configuration in locked_items}
                locked_boxes = {
                    box.pk: box for box in GiftBox.objects.select_for_update().filter(pk__in=box_ids)
                }
                locked_selections = list(
                    BoxSelection.objects.select_for_update()
                    .filter(configuration_id__in=[row.pk for row in locked_items])
                    .order_by('pk')
                )
                selections_by_configuration = {}
                required_box_stock = {}
                required_item_stock = {}
                for configuration in locked_items:
                    box = locked_boxes.get(configuration.gift_box_id)
                    if not box or not box.is_active:
                        raise ValidationError('A gift box in your cart is no longer available.')
                    configuration.gift_box = box
                    required_box_stock[box.pk] = (
                        required_box_stock.get(box.pk, 0) + configuration.quantity
                    )
                    selections = [
                        selection for selection in locked_selections
                        if selection.configuration_id == configuration.pk
                    ]
                    selections_by_configuration[configuration.pk] = selections
                    for selection in selections:
                        if not selection.box_item_id:
                            raise ValidationError(f'“{selection.item_name}” is no longer available.')
                        required_item_stock[selection.box_item_id] = (
                            required_item_stock.get(selection.box_item_id, 0)
                            + selection.quantity * configuration.quantity
                        )

                for box_id, quantity in required_box_stock.items():
                    box = locked_boxes[box_id]
                    if box.stock < quantity:
                        raise ValidationError(f'Not enough stock for gift box “{box.name}”.')

                stocked_items = {
                    item.pk: item for item in BoxItem.objects.select_for_update()
                    .filter(pk__in=required_item_stock, is_active=True)
                }
                if len(stocked_items) != len(required_item_stock):
                    raise ValidationError('A selected product is no longer available.')
                for item_id, quantity in required_item_stock.items():
                    item = stocked_items[item_id]
                    if item.stock < quantity:
                        raise ValidationError(f'Not enough stock for “{item.name}”.')

                for selections in selections_by_configuration.values():
                    for selection in selections:
                        catalog_item = stocked_items[selection.box_item_id]
                        selection.box_item = catalog_item
                        selection.freeze()
                        selection.save(update_fields=['item_name_at_order', 'unit_price_at_order'])

                subtotal = sum(
                    (
                        locked_boxes[configuration.gift_box_id].base_price
                        + sum(
                            (selection.unit_price * selection.quantity
                             for selection in selections_by_configuration[configuration.pk]),
                            Decimal('0.00'),
                        )
                    ) * configuration.quantity
                    for configuration in locked_items
                )
                tax_amount = (subtotal * settings.TAX_RATE).quantize(Decimal('0.01'))
                shipping_amount = (
                    Decimal('0.00') if subtotal >= settings.FREE_SHIPPING_THRESHOLD
                    else settings.FLAT_SHIPPING_FEE
                )
                order = Order.objects.create(
                    user=request.user,
                    contact_full_name=checkout_data['full_name'],
                    contact_email=checkout_data['email'],
                    contact_phone=checkout_data['phone'],
                    ship_to_address=checkout_data['shipping_address'],
                    ship_to_city=checkout_data['city'],
                    ship_to_state=checkout_data['state'],
                    ship_to_postal_code=checkout_data['postal_code'],
                    tax_amount=tax_amount,
                    shipping_amount=shipping_amount,
                    delivery_date=checkout_data.get('delivery_date') or None,
                    payment_method=payment_method,
                    payment_status='pending' if payment_method == 'cod' else 'paid',
                    order_status='pending' if payment_method == 'cod' else 'paid',
                )
                for configuration in locked_items:
                    configuration.freeze()
                    configuration.order = order
                    configuration.status = BoxConfiguration.ORDERED
                    configuration.gift_box.stock = F('stock') - configuration.quantity
                    configuration.gift_box.save(update_fields=['stock'])
                    configuration.gift_box.refresh_from_db(fields=['stock'])
                    configuration.save(update_fields=[
                        'box_name_at_order', 'base_price_at_order',
                        'unit_price_at_order', 'order', 'status',
                    ])

                for item_id, quantity in required_item_stock.items():
                    BoxItem.objects.filter(pk=item_id).update(stock=F('stock') - quantity)

                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                profile.phone = checkout_data['phone']
                profile.address_line1 = checkout_data['shipping_address']
                profile.city = checkout_data['city']
                profile.state = checkout_data['state']
                profile.postal_code = checkout_data['postal_code']
                profile.save()

        request.session.pop('checkout_data', None)
        messages.success(request, f'Order #{order.order_number} has been placed.')
        if not send_order_confirmation_email(order):
            messages.warning(
                request,
                'The order was placed, but its confirmation email could not be sent. Please save the order number.',
            )
        return redirect('order_confirmation', order_number=order.order_number)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('cart')
    except Exception:
        logger.exception('Order placement failed user_id=%s', request.user.pk)
        messages.error(request, 'We could not place your order. Please try again.')
        return redirect('checkout')


@login_required
def order_confirmation_view(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    return render(request, 'order_confirmation.html', {'order': order})


# ==============================================================================
# CUSTOMER PROFILE, ORDER HISTORY & REORDER
# ==============================================================================

@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    orders = Order.objects.filter(user=request.user).prefetch_related('configurations__selections')
    status_filter = request.GET.get('status', '')
    if status_filter in dict(Order.STATUS_CHOICES):
        orders = orders.filter(order_status=status_filter)
    elif status_filter:
        messages.error(request, 'Select a valid order status.')
        status_filter = ''
    orders, start_date, end_date = apply_order_date_filters(orders, request)
    drafts = BoxConfiguration.objects.filter(
        user=request.user, status=BoxConfiguration.DRAFT,
    ).select_related('gift_box')
    available_gift_boxes = GiftBox.objects.filter(
        is_active=True,
    ).select_related('event').order_by('name')
    draft_sort = request.GET.get('draft_sort', 'recent')
    if draft_sort == 'name':
        drafts = drafts.order_by('draft_name', 'updated_at')
    elif draft_sort == 'oldest':
        drafts = drafts.order_by('updated_at')
    else:
        draft_sort = 'recent'
        drafts = drafts.order_by('-updated_at')

    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=user_profile)
        if form.is_valid():
            # Update user model fields
            request.user.first_name = form.cleaned_data.get('full_name', '').split(' ')[0]
            request.user.last_name = ' '.join(form.cleaned_data.get('full_name', '').split(' ')[1:])
            request.user.email = form.cleaned_data.get('email')
            request.user.save()

            form.save()
            messages.success(request, '✅ Profile information updated successfully!')
            return redirect('profile')
    else:
        initial = {
            'full_name': request.user.get_full_name() or request.user.username,
            'email': request.user.email,
        }
        form = ProfileForm(instance=user_profile, initial=initial)

    return render(request, 'profile.html', {
        'form': form,
        'active_tab': 'account-tab' if request.method == 'POST' and form.errors else 'orders-tab',
        'orders': orders,
        'drafts': drafts,
        'available_gift_boxes': available_gift_boxes,
        'status_filter': status_filter,
        'start_date': start_date,
        'end_date': end_date,
        'order_status_choices': Order.STATUS_CHOICES,
        'draft_sort': draft_sort,
    })


@login_required
def order_detail_view(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    if not (order.user == request.user or request.user.is_staff):
        messages.error(request, 'Unauthorized access to order.')
        return redirect('profile')

    return render(request, 'order_detail.html', {'order': order})


@login_required
def order_invoice_pdf(request, order_number):
    order = get_object_or_404(
        Order.objects.prefetch_related('configurations__selections'),
        order_number=order_number,
    )
    if not (order.user == request.user or request.user.is_staff):
        messages.error(request, 'Unauthorized access to order.')
        return redirect('profile')

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="invoice-{order.order_number}.pdf"'
    )
    document = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f'Invoice {order.order_number}',
        author='GLINT Personalized Gift Collection',
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='InvoiceSmall',
        parent=styles['BodyText'],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#555555'),
    ))
    story = [
        Paragraph('GLINT Personalized Gift Collection', styles['Title']),
        Paragraph('Order Invoice &amp; Receipt', styles['Heading2']),
        Spacer(1, 5 * mm),
    ]

    metadata = [
        ['Invoice / Order Number', escape(order.order_number)],
        ['Order Date', order.created_at.strftime('%B %d, %Y %I:%M %p')],
        ['Order Status', escape(order.get_order_status_display())],
        ['Payment', (
            f'{escape(order.get_payment_status_display())} via '
            f'{escape(order.get_payment_method_display())}'
        )],
    ]
    story.append(Table(metadata, colWidths=[44 * mm, 130 * mm], hAlign='LEFT'))
    story.append(Spacer(1, 5 * mm))

    sender = (
        f'<b>Billed to</b><br/>{escape(order.full_name)}<br/>'
        f'{escape(order.email)}<br/>{escape(order.phone)}'
    )
    delivery = (
        f'<b>Deliver to</b><br/>{escape(order.shipping_address)}<br/>'
        f'{escape(order.city)}, {escape(order.state)} {escape(order.postal_code)}'
    )
    story.append(Table(
        [[Paragraph(sender, styles['BodyText']), Paragraph(delivery, styles['BodyText'])]],
        colWidths=[87 * mm, 87 * mm],
        hAlign='LEFT',
    ))
    story.append(Spacer(1, 7 * mm))

    item_rows = [[
        Paragraph('<b>Gift box and personalization</b>', styles['InvoiceSmall']),
        Paragraph('<b>Qty</b>', styles['InvoiceSmall']),
        Paragraph('<b>Unit Price</b>', styles['InvoiceSmall']),
        Paragraph('<b>Total</b>', styles['InvoiceSmall']),
    ]]
    for item in order.configurations.all():
        details = [f'<b>{escape(item.box_name)}</b>']
        if item.personalization_name:
            details.append(f'Recipient: {escape(item.personalization_name)}')
        if item.personalization_message:
            details.append(f'Message: {escape(item.personalization_message)}')
        selections = [
            f'{escape(selection.item_name)} ({selection.quantity} x '
            f'INR {selection.unit_price:,.2f})'
            for selection in item.selections.all()
        ]
        if selections:
            details.append('Selected items: ' + ', '.join(selections))
        item_rows.append([
            Paragraph('<br/>'.join(details), styles['InvoiceSmall']),
            str(item.quantity),
            f'INR {item.unit_price:,.2f}',
            f'INR {item.total_price:,.2f}',
        ])

    items_table = Table(
        item_rows,
        colWidths=[90 * mm, 14 * mm, 34 * mm, 34 * mm],
        repeatRows=1,
        hAlign='LEFT',
    )
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1edf4')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9d2df')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.extend([items_table, Spacer(1, 5 * mm)])

    totals = [
        ['Subtotal', f'INR {order.subtotal:,.2f}'],
        ['Tax', f'INR {order.tax_amount:,.2f}'],
        ['Shipping', 'FREE' if order.shipping_amount == 0 else f'INR {order.shipping_amount:,.2f}'],
        ['Grand Total', f'INR {order.grand_total:,.2f}'],
    ]
    totals_table = Table(totals, colWidths=[45 * mm, 45 * mm], hAlign='RIGHT')
    totals_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#473050')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.extend([
        totals_table,
        Spacer(1, 8 * mm),
        Paragraph(
            'Thank you for ordering with GLINT. This application simulates '
            'Checkout is in test mode; no real payment was collected.',
            styles['InvoiceSmall'],
        ),
    ])
    document.build(story)
    return response


@login_required
@require_POST
def cancel_customer_order(request, order_number):
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            order_number=order_number,
            user=request.user,
        )
        if order.order_status in ['pending', 'paid']:
            old_status = order.order_status
            restore_order_inventory(order)
            order.order_status = 'cancelled'
            order.payment_status = 'refunded' if order.payment_status == 'paid' else 'pending'
            order.save(update_fields=['order_status', 'payment_status'])
            transaction.on_commit(lambda: send_order_status_email(order, old_status))
            messages.success(request, f'Order #{order.order_number} has been cancelled.')
        else:
            messages.error(request, f'Order cannot be cancelled because its current status is {order.get_order_status_display()}.')
    return redirect('profile')


# ==============================================================================
# CUSTOM ADMIN PORTAL & ANALYTICS VIEWS
# ==============================================================================

@user_passes_test(is_admin, login_url='login')
def admin_dashboard(request):
    """Admin Analytics & KPI Dashboard with Chart.js support."""
    orders, start_date, end_date = apply_order_date_filters(Order.objects.all(), request)
    total_orders = orders.count()
    paid_orders = list(
        orders.filter(payment_status='paid').prefetch_related('configurations__selections')
    )
    total_revenue = Decimal('0.00')
    monthly_revenue = {}
    for order in paid_orders:
        total_revenue += order.grand_total
        month_key = order.created_at.strftime('%Y-%m')
        if month_key not in monthly_revenue:
            monthly_revenue[month_key] = {
                'label': order.created_at.strftime('%b %Y'),
                'total': Decimal('0.00'),
            }
        monthly_revenue[month_key]['total'] += order.grand_total

    total_customers = User.objects.filter(is_staff=False).count()
    paid_order_count = len(paid_orders)
    aov = (total_revenue / paid_order_count) if paid_order_count > 0 else Decimal('0.00')
    paid_order_ids = [order.pk for order in paid_orders]

    # Status Breakdown
    status_counts = orders.values('order_status').annotate(count=Count('id'))
    status_dict = {item['order_status']: item['count'] for item in status_counts}

    # Revenue by Occasion
    ordered_configurations = BoxConfiguration.objects.filter(
        status=BoxConfiguration.ORDERED,
        order_id__in=paid_order_ids,
    ).prefetch_related('selections')
    occasion_totals = {}
    box_totals = {}
    for configuration in ordered_configurations.select_related('gift_box__event'):
        event_name = configuration.gift_box.event.name if configuration.gift_box_id else 'Custom'
        amount = configuration.get_total_price()
        occasion_totals[event_name] = occasion_totals.get(event_name, Decimal('0.00')) + amount
        name = configuration.box_name or 'Gift Box'
        quantity, revenue = box_totals.get(name, (0, Decimal('0.00')))
        box_totals[name] = (
            quantity + configuration.quantity,
            revenue + amount,
        )
    occasion_sales = [
        {'gift_box__event__name': name, 'total': total}
        for name, total in sorted(occasion_totals.items(), key=lambda row: row[1], reverse=True)[:6]
    ]
    top_boxes = [
        {'box_name': name, 'total_sold': quantity, 'revenue': revenue}
        for name, (quantity, revenue) in sorted(
            box_totals.items(), key=lambda row: row[1][0], reverse=True
        )[:5]
    ]

    recent_orders = orders.order_by('-created_at')[:8]
    top_items = BoxSelection.objects.filter(
        configuration__order_id__in=paid_order_ids,
    ).values(item_name=F('item_name_at_order')).annotate(
        total_sold=Sum(F('quantity') * F('configuration__quantity')),
    ).order_by('-total_sold')[:5]
    top_categories = BoxSelection.objects.filter(
        configuration__order_id__in=paid_order_ids,
        box_item__catalog_assignments__category__isnull=False,
    ).values(
        'box_item__catalog_assignments__category__name',
    ).annotate(
        total_sold=Sum(F('quantity') * F('configuration__quantity')),
    ).order_by('-total_sold')[:5]

    # Chart data formatting for Chart.js
    occasion_labels = [item['gift_box__event__name'] or 'Custom' for item in occasion_sales]
    occasion_values = [float(item['total'] or 0) for item in occasion_sales]
    monthly_revenue = [
        monthly_revenue[month]
        for month in sorted(monthly_revenue)
    ]

    return render(request, 'admin_portal/dashboard.html', {
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'total_customers': total_customers,
        'aov': round(aov, 2),
        'status_dict': status_dict,
        'recent_orders': recent_orders,
        'top_boxes': top_boxes,
        'top_items': top_items,
        'top_categories': top_categories,
        'occasion_labels_json': json.dumps(occasion_labels),
        'occasion_values_json': json.dumps(occasion_values),
        'monthly_labels_json': json.dumps([item['label'] for item in monthly_revenue]),
        'monthly_values_json': json.dumps([float(item['total']) for item in monthly_revenue]),
        'start_date': start_date,
        'end_date': end_date,
    })


@user_passes_test(is_admin, login_url='login')
def admin_products_list(request):
    boxes = GiftBox.objects.all().select_related('event').order_by('name')
    items = BoxItem.objects.all().order_by('name')
    categories = Category.objects.filter(is_active=True)
    occasions = EventOccasion.objects.all()

    query = request.GET.get('q', '').strip()
    category_filter = request.GET.get('category', '').strip()
    min_price = request.GET.get('min_price', '').strip()
    max_price = request.GET.get('max_price', '').strip()
    stock_filter = request.GET.get('stock', '').strip()
    sort_by = request.GET.get('sort', 'name')

    if query:
        boxes = boxes.filter(Q(name__icontains=query))
        items = items.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(catalog_assignments__category__name__icontains=query)
        ).distinct()

    if category_filter:
        items = items.filter(catalog_assignments__category__name=category_filter).distinct()
    parsed_prices = []
    valid_prices = True
    for raw_price, lookup in ((min_price, 'gte'), (max_price, 'lte')):
        if raw_price:
            try:
                price = Decimal(raw_price)
                if not price.is_finite() or price < 0:
                    raise InvalidOperation
            except InvalidOperation:
                messages.error(request, 'Price filters must be non-negative numbers.')
                valid_prices = False
                break
            parsed_prices.append((lookup, price))
    if valid_prices:
        for lookup, price in parsed_prices:
            boxes = boxes.filter(**{f'base_price__{lookup}': price})
            items = items.filter(**{f'price__{lookup}': price})

    if stock_filter == 'out':
        boxes = boxes.filter(stock=0)
        items = items.filter(stock=0)
    elif stock_filter == 'low':
        boxes = boxes.filter(stock__gt=0, stock__lt=10)
        items = items.filter(stock__gt=0, stock__lt=10)
    elif stock_filter == 'available':
        boxes = boxes.filter(stock__gt=0)
        items = items.filter(stock__gt=0)
    elif stock_filter:
        messages.error(request, 'Select a valid stock filter.')
        stock_filter = ''

    sort_fields = {
        'name': ('name', 'name'),
        'price_low': ('base_price', 'price'),
        'price_high': ('-base_price', '-price'),
        'stock_low': ('stock', 'stock'),
        'stock_high': ('-stock', '-stock'),
    }
    if sort_by not in sort_fields:
        messages.error(request, 'Select a valid product sort order.')
        sort_by = 'name'
    box_sort, item_sort = sort_fields[sort_by]
    boxes = boxes.order_by(box_sort)
    items = items.order_by(item_sort)

    return render(request, 'admin_portal/products.html', {
        'boxes': boxes,
        'items': items,
        'categories': categories,
        'occasions': occasions,
        'query': query,
        'category_filter': category_filter,
        'min_price': min_price,
        'max_price': max_price,
        'stock_filter': stock_filter,
        'sort_by': sort_by,
    })


@user_passes_test(is_admin, login_url='login')
def admin_box_create(request):
    if request.method == 'POST':
        form = GiftBoxForm(request.POST, request.FILES)
        if form.is_valid():
            box = form.save()
            log_admin_action(request.user, f"Created Gift Box: {box.name}", request)
            messages.success(request, f"🎉 Gift Box '{box.name}' created successfully.")
            return redirect('admin_products')
    else:
        form = GiftBoxForm()
    return render(request, 'admin_portal/product_form.html', {'form': form, 'title': 'Create New Gift Box'})


@user_passes_test(is_admin, login_url='login')
def admin_box_edit(request, pk):
    box = get_object_or_404(GiftBox, pk=pk)
    if request.method == 'POST':
        form = GiftBoxForm(request.POST, request.FILES, instance=box)
        if form.is_valid():
            box = form.save()
            log_admin_action(request.user, f"Edited Gift Box: {box.name}", request)
            messages.success(request, f"✅ Gift Box '{box.name}' updated.")
            return redirect('admin_products')
    else:
        form = GiftBoxForm(instance=box)
    return render(request, 'admin_portal/product_form.html', {'form': form, 'title': f'Edit {box.name}'})


@user_passes_test(is_admin, login_url='login')
@require_POST
def admin_box_delete(request, pk):
    box = get_object_or_404(GiftBox, pk=pk)
    name = box.name
    box.delete()
    log_admin_action(request.user, f"Deleted Gift Box: {name}", request)
    messages.success(request, f"🗑️ Gift Box '{name}' deleted.")
    return redirect('admin_products')


@user_passes_test(is_admin, login_url='login')
def admin_box_item_create(request):
    if request.method == 'POST':
        form = BoxItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save()
            log_admin_action(request.user, f"Created Item: {item.name}", request)
            messages.success(request, f"✨ Customizable Item '{item.name}' created.")
            return redirect('admin_products')
    else:
        form = BoxItemForm()
    return render(request, 'admin_portal/product_form.html', {'form': form, 'title': 'Create New Box Item'})


@user_passes_test(is_admin, login_url='login')
def admin_box_item_edit(request, pk):
    item = get_object_or_404(BoxItem, pk=pk)
    if request.method == 'POST':
        form = BoxItemForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            item = form.save()
            log_admin_action(request.user, f"Edited Item: {item.name}", request)
            messages.success(request, f"✅ Item '{item.name}' updated.")
            return redirect('admin_products')
    else:
        form = BoxItemForm(instance=item)
    return render(request, 'admin_portal/product_form.html', {'form': form, 'title': f'Edit Item {item.name}'})


@user_passes_test(is_admin, login_url='login')
@require_POST
def admin_box_item_delete(request, pk):
    item = get_object_or_404(BoxItem, pk=pk)
    name = item.name
    item.delete()
    log_admin_action(request.user, f"Deleted Item: {name}", request)
    messages.success(request, f"🗑️ Item '{name}' deleted.")
    return redirect('admin_products')


@user_passes_test(is_admin, login_url='login')
def admin_orders_list(request):
    orders = Order.objects.all().order_by('-created_at')
    status_filter = request.GET.get('status')
    query = request.GET.get('q', '').strip()

    if status_filter in dict(Order.STATUS_CHOICES):
        orders = orders.filter(order_status=status_filter)
    elif status_filter:
        messages.error(request, 'Select a valid order status.')
        status_filter = ''

    if query:
        orders = orders.filter(
            Q(order_number__icontains=query) |
            Q(contact_full_name__icontains=query) |
            Q(contact_email__icontains=query) |
            Q(contact_phone__icontains=query)
        )

    orders, start_date, end_date = apply_order_date_filters(orders, request)
    return render(request, 'admin_portal/orders.html', {
        'orders': orders,
        'status_filter': status_filter,
        'query': query,
        'start_date': start_date,
        'end_date': end_date,
    })


@user_passes_test(is_admin, login_url='login')
def admin_order_detail(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    if request.method == 'POST':
        form = AdminOrderUpdateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = get_object_or_404(
                    Order.objects.select_for_update(), order_number=order_number,
                )
                old_status = order.order_status
                old_tracking_number = order.tracking_number
                new_status = form.cleaned_data['order_status']
                if old_status == 'cancelled' and new_status != 'cancelled':
                    form.add_error('order_status', 'A cancelled order cannot be reopened.')
                else:
                    if old_status != 'cancelled' and new_status == 'cancelled':
                        restore_order_inventory(order)
                        if order.payment_status == 'paid':
                            form.cleaned_data['payment_status'] = 'refunded'
                    for field, value in form.cleaned_data.items():
                        setattr(order, field, value)
                    order.save()
                    log_admin_action(
                        request.user,
                        f"Updated Order #{order.order_number} to {order.order_status}",
                        request,
                    )
                    transaction.on_commit(
                        lambda: send_order_status_email(order, old_status, old_tracking_number)
                    )
                    messages.success(request, f"Order #{order.order_number} updated.")
                    return redirect('admin_order_detail', order_number=order.order_number)
    else:
        form = AdminOrderUpdateForm(initial={
            'order_status': order.order_status,
            'payment_status': order.payment_status,
            'tracking_number': order.tracking_number,
            'internal_notes': order.internal_notes,
            'delivery_date': order.delivery_date,
        })

    return render(request, 'admin_portal/order_detail.html', {'order': order, 'form': form})


@user_passes_test(is_admin, login_url='login')
def admin_packing_slip(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    return render(request, 'admin_portal/packing_slip.html', {'order': order})


@user_passes_test(is_admin, login_url='login')
def admin_export_orders_csv(request):
    status_filter = request.GET.get('status', '')
    if status_filter and status_filter not in dict(Order.STATUS_CHOICES):
        messages.error(request, 'Select a valid order status before exporting.')
        return redirect('admin_orders')

    orders = Order.objects.all()
    if status_filter:
        orders = orders.filter(order_status=status_filter)
    orders, start_date, end_date = apply_order_date_filters(orders, request)
    if (
        (request.GET.get('start_date') and not start_date)
        or (request.GET.get('end_date') and not end_date)
    ):
        return redirect('admin_orders')

    def safe_cell(value):
        if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
            return "'" + value
        return value

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="glint_orders_{timezone.now().strftime("%Y%m%d_%H%M")}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Order Number', 'Date', 'Customer Name', 'Email', 'Phone',
        'Address', 'City', 'State', 'Postal Code',
        'Subtotal', 'Tax', 'Shipping', 'Grand Total',
        'Payment Method', 'Payment Status', 'Order Status'
    ])

    for order in orders.order_by('-created_at').prefetch_related('configurations__selections'):
        writer.writerow([
            safe_cell(order.order_number),
            order.created_at.strftime('%Y-%m-%d %H:%M'),
            safe_cell(order.full_name),
            safe_cell(order.email),
            safe_cell(order.phone),
            safe_cell(order.shipping_address),
            safe_cell(order.city),
            safe_cell(order.state),
            safe_cell(order.postal_code),
            order.subtotal,
            order.tax_amount,
            order.shipping_amount,
            order.grand_total,
            safe_cell(order.get_payment_method_display()),
            safe_cell(order.get_payment_status_display()),
            safe_cell(order.get_order_status_display()),
        ])

    return response