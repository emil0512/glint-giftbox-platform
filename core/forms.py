import re
from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import transaction
from django.utils import timezone
from .models import (
    UserProfile, GiftBox, BoxItem, Category, CatalogAssignment, EventOccasion, Order,
)


# ==============================================================================
# REUSABLE CUSTOM VALIDATORS & REGEXES
# ==============================================================================

# Name validation: letters, spaces, hyphens, apostrophes, and dots only (NO numbers)
NAME_REGEX = r"^[A-Za-z\s\.\'\-]+$"
name_validator = RegexValidator(
    regex=NAME_REGEX,
    message="Name must only contain letters, spaces, hyphens, and dots. Numbers are not allowed."
)

# City / State validation: letters and spaces only
TEXT_ONLY_REGEX = r"^[A-Za-z\s\.\-]+$"
text_only_validator = RegexValidator(
    regex=TEXT_ONLY_REGEX,
    message="This field must only contain letters and spaces. Numbers are not allowed."
)


def validate_strict_password(password):
    """
    Strict password validation:
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    if len(password) < 8:
        raise ValidationError('Password must be at least 8 characters long.')
    if not re.search(r'[A-Z]', password):
        raise ValidationError('Password must contain at least one uppercase letter (A-Z).')
    if not re.search(r'[a-z]', password):
        raise ValidationError('Password must contain at least one lowercase letter (a-z).')
    if not re.search(r'[0-9]', password):
        raise ValidationError('Password must contain at least one number (0-9).')
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?~`]', password):
        raise ValidationError('Password must contain at least one special character (!@#$%^&* etc.).')
    return password


def validate_phone_number(phone_raw):
    """
    Phone number validation:
    - Exactly 10 digits (after stripping spaces, dashes, +91)
    - Cannot be all zeros (e.g. 0000000000 is invalid)
    - Cannot be all same repeating digits (e.g. 1111111111)
    """
    if not phone_raw:
        raise ValidationError('Phone number is required.')

    # Strip country code, spaces, dashes, parentheses
    cleaned = re.sub(r'[\s\-\+\(\)]', '', str(phone_raw))
    if cleaned.startswith('91') and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith('0') and len(cleaned) == 11:
        cleaned = cleaned[1:]

    if not cleaned.isdigit():
        raise ValidationError('Phone number must contain numeric digits only.')

    if len(cleaned) != 10:
        raise ValidationError('Phone number must be exactly 10 digits.')

    if cleaned == '0' * 10:
        raise ValidationError('Phone number cannot be all zeros.')

    if len(set(cleaned)) == 1:
        raise ValidationError('Phone number cannot consist of the same repeating digit.')

    if cleaned[0] not in '56789':
        raise ValidationError('Please enter a valid 10-digit mobile number (starting with 5-9).')

    return cleaned


def validate_postal_code(postal_raw):
    """
    PIN / Postal code validation:
    - Exactly 6 digits (standard Indian PIN)
    - Cannot be all zeros
    """
    if not postal_raw:
        raise ValidationError('PIN / Postal code is required.')

    cleaned = re.sub(r'\s', '', str(postal_raw))
    if not cleaned.isdigit():
        raise ValidationError('PIN / Postal code must contain digits only.')

    if len(cleaned) != 6:
        raise ValidationError('PIN code must be exactly 6 digits.')

    if cleaned == '000000':
        raise ValidationError('PIN code cannot be all zeros.')

    return cleaned


# ==============================================================================
# FORMS
# ==============================================================================

class RegisterForm(forms.Form):
    full_name = forms.CharField(
        max_length=100,
        required=True,
        validators=[name_validator],
        widget=forms.TextInput(attrs={
            'placeholder': 'Your full name',
            'class': 'form-control-custom',
            'autocomplete': 'name',
        })
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'placeholder': 'Enter your email address',
            'class': 'form-control-custom',
            'autocomplete': 'email',
        })
    )
    password = forms.CharField(
        min_length=8,
        required=True,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Min 8 chars with Upper, Lower, Number & Special char',
            'class': 'form-control-custom',
            'autocomplete': 'new-password',
        })
    )
    password_confirm = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm your password',
            'class': 'form-control-custom',
            'autocomplete': 'new-password',
        })
    )
    terms_agreed = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must agree to the Terms of Service & Privacy Policy.'}
    )

    def clean_full_name(self):
        name = self.cleaned_data.get('full_name', '').strip()
        if len(name) < 2:
            raise ValidationError('Full name must be at least 2 characters long.')
        if re.search(r'\d', name):
            raise ValidationError('Full name cannot contain any numbers.')
        return name

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account with this email already exists.')
        return email

    def clean_password(self):
        password = self.cleaned_data.get('password', '')
        return validate_strict_password(password)

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Passwords do not match.')
        return cleaned_data


class LoginForm(forms.Form):
    username_or_email = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'Username or Email',
            'class': 'form-control-custom',
            'autocomplete': 'username',
        })
    )
    password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter your password',
            'class': 'form-control-custom',
            'autocomplete': 'current-password',
        })
    )
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_username_or_email(self):
        val = self.cleaned_data.get('username_or_email', '').strip()
        if not val:
            raise ValidationError('Please enter your username or email.')
        return val


class ForgotPasswordRequestForm(forms.Form):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'placeholder': 'Enter your registered email address',
            'class': 'form-control-custom',
            'autocomplete': 'email',
        })
    )

    def clean_email(self):
        return self.cleaned_data.get('email', '').strip().lower()


class VerifyOtpAndResetPasswordForm(forms.Form):
    otp = forms.CharField(
        max_length=6,
        min_length=6,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'Enter 6-digit OTP',
            'class': 'form-control-custom text-center fw-bold fs-4',
            'maxlength': '6',
            'pattern': '[0-9]{6}',
            'autocomplete': 'one-time-code',
        })
    )
    new_password = forms.CharField(
        min_length=8,
        required=True,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'New password (min 8 chars, Upper, Lower, Number, Special)',
            'class': 'form-control-custom',
            'autocomplete': 'new-password',
        })
    )
    confirm_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm new password',
            'class': 'form-control-custom',
            'autocomplete': 'new-password',
        })
    )

    def clean_otp(self):
        otp = self.cleaned_data.get('otp', '').strip()
        if not otp.isdigit() or len(otp) != 6:
            raise ValidationError('OTP must be exactly 6 numeric digits.')
        return otp

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password', '')
        return validate_strict_password(password)

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')

        if new_password and confirm_password and new_password != confirm_password:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned_data


class ProfileForm(forms.ModelForm):
    full_name = forms.CharField(
        max_length=100,
        required=True,
        validators=[name_validator],
        widget=forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'Full Name'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control-custom', 'placeholder': 'Email Address'})
    )

    class Meta:
        model = UserProfile
        fields = ['phone', 'address_line1', 'city', 'state', 'postal_code']
        widgets = {
            'phone': forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': '10-digit mobile number', 'maxlength': '15'}),
            'address_line1': forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'Street Address / Apartment / House #'}),
            'city': forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'City'}),
            'state': forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'State'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': '6-digit PIN Code', 'maxlength': '6'}),
        }

    def clean_full_name(self):
        name = self.cleaned_data.get('full_name', '').strip()
        if len(name) < 2:
            raise ValidationError('Full name must be at least 2 characters long.')
        if re.search(r'\d', name):
            raise ValidationError('Full name cannot contain any numbers.')
        return name

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        conflicting_users = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            conflicting_users = conflicting_users.exclude(pk=self.instance.user_id)
        if conflicting_users.exists():
            raise ValidationError('An account with this email already exists.')
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '')
        return validate_phone_number(phone)

    def clean_city(self):
        city = self.cleaned_data.get('city', '').strip()
        if not city:
            raise ValidationError('City is required.')
        if re.search(r'\d', city):
            raise ValidationError('City name cannot contain numbers.')
        return city

    def clean_state(self):
        state = self.cleaned_data.get('state', '').strip()
        if not state:
            raise ValidationError('State is required.')
        if re.search(r'\d', state):
            raise ValidationError('State name cannot contain numbers.')
        return state

    def clean_postal_code(self):
        postal_code = self.cleaned_data.get('postal_code', '')
        return validate_postal_code(postal_code)

    def clean_address_line1(self):
        addr = self.cleaned_data.get('address_line1', '').strip()
        if not addr or len(addr) < 5:
            raise ValidationError('Please provide a complete street address (min 5 characters).')
        return addr


class CheckoutForm(forms.Form):
    full_name = forms.CharField(
        max_length=100,
        required=True,
        validators=[name_validator],
        widget=forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'Full Name of Recipient / Sender'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control-custom', 'placeholder': 'Email Address for Order Confirmation'})
    )
    phone = forms.CharField(
        max_length=20,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': '10-digit mobile number', 'maxlength': '15'})
    )
    shipping_address = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'class': 'form-control-custom', 'rows': 3, 'placeholder': 'Complete Street Address, Apartment/House No, Landmark'})
    )
    city = forms.CharField(
        max_length=80,
        required=True,
        validators=[text_only_validator],
        widget=forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'City'})
    )
    state = forms.CharField(
        max_length=80,
        required=True,
        validators=[text_only_validator],
        widget=forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': 'State'})
    )
    postal_code = forms.CharField(
        max_length=10,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control-custom', 'placeholder': '6-digit PIN Code', 'maxlength': '6'})
    )
    payment_method = forms.ChoiceField(
        choices=Order.PAYMENT_METHOD_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'payment-radio'}),
        initial='card'
    )
    delivery_date = forms.DateField(
        required=False,
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(attrs={
            'class': 'form-control-custom',
            'type': 'date',
            'min': timezone.localdate().isoformat(),
        }),
    )

    def clean_full_name(self):
        name = self.cleaned_data.get('full_name', '').strip()
        if len(name) < 2:
            raise ValidationError('Full name must be at least 2 characters.')
        if re.search(r'\d', name):
            raise ValidationError('Full name cannot contain numbers.')
        return name

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '')
        return validate_phone_number(phone)

    def clean_city(self):
        city = self.cleaned_data.get('city', '').strip()
        if not city:
            raise ValidationError('City is required.')
        if re.search(r'\d', city):
            raise ValidationError('City cannot contain numbers.')
        return city

    def clean_state(self):
        state = self.cleaned_data.get('state', '').strip()
        if not state:
            raise ValidationError('State is required.')
        if re.search(r'\d', state):
            raise ValidationError('State cannot contain numbers.')
        return state

    def clean_postal_code(self):
        postal_code = self.cleaned_data.get('postal_code', '')
        return validate_postal_code(postal_code)

    def clean_shipping_address(self):
        addr = self.cleaned_data.get('shipping_address', '').strip()
        if not addr or len(addr) < 5:
            raise ValidationError('Delivery address must be at least 5 characters long.')
        return addr

    def clean_delivery_date(self):
        delivery_date = self.cleaned_data.get('delivery_date')
        if delivery_date and delivery_date < timezone.localdate():
            raise ValidationError('Delivery date cannot be in the past.')
        return delivery_date


class GiftBoxForm(forms.ModelForm):
    class Meta:
        model = GiftBox
        fields = ['name', 'event', 'description', 'image', 'emoji', 'base_price', 'stock', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control-custom'}),
            'event': forms.Select(attrs={'class': 'form-control-custom'}),
            'description': forms.Textarea(attrs={'class': 'form-control-custom', 'rows': 3}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control-custom'}),
            'emoji': forms.TextInput(attrs={'class': 'form-control-custom', 'maxlength': '16'}),
            'base_price': forms.NumberInput(attrs={'class': 'form-control-custom', 'step': '0.01'}),
            'stock': forms.NumberInput(attrs={'class': 'form-control-custom'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_base_price(self):
        price = self.cleaned_data.get('base_price')
        if price is None or price <= 0:
            raise ValidationError('Base price must be a positive number greater than zero.')
        return price

    def clean_stock(self):
        stock = self.cleaned_data.get('stock')
        if stock is None or stock < 0:
            raise ValidationError('Stock quantity cannot be negative.')
        return stock

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and image.size > 5 * 1024 * 1024:
            raise ValidationError('Gift box images must be under 5 MB.')
        return image


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]
        cleaned = []
        for image in data:
            value = super().clean(image, initial)
            if value.size > 5 * 1024 * 1024:
                raise ValidationError('Each image must be under 5 MB.')
            cleaned.append(value)
        return cleaned


class BoxItemForm(forms.ModelForm):
    categories = forms.CharField(
        max_length=1000,
        widget=forms.TextInput(attrs={
            'class': 'form-control-custom',
            'placeholder': 'Chocolates, Flowers (comma-separated)',
        }),
    )
    gift_boxes = forms.ModelMultipleChoiceField(
        queryset=GiftBox.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-control-custom', 'style': 'height: 120px;'}),
    )
    default_for_boxes = forms.ModelMultipleChoiceField(
        queryset=GiftBox.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-control-custom', 'style': 'height: 120px;'}),
    )
    events = forms.ModelMultipleChoiceField(
        queryset=EventOccasion.objects.filter(is_active=True),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-control-custom', 'style': 'height: 120px;'}),
        label='Recommended for occasions',
    )
    images = MultipleImageField(required=False, label='Product images')
    remove_images = forms.MultipleChoiceField(
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        label='Remove existing images',
    )

    class Meta:
        model = BoxItem
        fields = ['name', 'description', 'price', 'stock', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control-custom'}),
            'description': forms.Textarea(attrs={'class': 'form-control-custom', 'rows': 3}),
            'price': forms.NumberInput(attrs={'class': 'form-control-custom', 'step': '0.01'}),
            'stock': forms.NumberInput(attrs={'class': 'form-control-custom'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            assignments = CatalogAssignment.objects.filter(box_item=self.instance)
            self.fields['gift_boxes'].initial = assignments.filter(
                gift_box__isnull=False
            ).values_list('gift_box_id', flat=True)
            self.fields['default_for_boxes'].initial = assignments.filter(
                gift_box__isnull=False, is_default=True
            ).values_list('gift_box_id', flat=True)
            self.fields['events'].initial = assignments.filter(
                event__isnull=False
            ).values_list('event_id', flat=True)
            category_names = Category.objects.filter(
                catalog_assignments__box_item=self.instance
            ).values_list('name', flat=True)
            self.fields['categories'].initial = ', '.join(category_names)
            images = CatalogAssignment.objects.filter(
                box_item=self.instance, image__isnull=False
            )
            self.fields['remove_images'].choices = [
                (image.pk, image.image.name.rsplit('/', 1)[-1])
                for image in images
            ]

    def clean(self):
        cleaned_data = super().clean()
        category_names = [
            name.strip() for name in cleaned_data.get('categories', '').split(',')
            if name.strip()
        ]
        if not category_names:
            self.add_error('categories', 'Assign the product to at least one category.')
        cleaned_data['category_names'] = list(dict.fromkeys(category_names))
        available_boxes = set(cleaned_data.get('gift_boxes', []))
        default_boxes = set(cleaned_data.get('default_for_boxes', []))
        if not default_boxes.issubset(available_boxes):
            self.add_error('default_for_boxes', 'Default boxes must also be selected as available boxes.')
        return cleaned_data

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        matching_items = BoxItem.objects.filter(name__iexact=name)
        if self.instance.pk:
            matching_items = matching_items.exclude(pk=self.instance.pk)
        if matching_items.exists():
            raise ValidationError('A product with this name already exists.')
        return name

    def save(self, commit=True):
        with transaction.atomic():
            instance = super().save(commit=commit)
            if commit:
                self.save_box_assignments()
            return instance

    def save_box_assignments(self):
        CatalogAssignment.objects.filter(
            box_item=self.instance,
            category__isnull=False,
        ).delete()
        CatalogAssignment.objects.filter(
            box_item=self.instance,
            gift_box__isnull=False,
        ).delete()
        CatalogAssignment.objects.filter(
            box_item=self.instance,
            event__isnull=False,
        ).delete()
        for name in self.cleaned_data['category_names']:
            category, _ = Category.objects.get_or_create(name=name)
            CatalogAssignment.objects.create(
                box_item=self.instance,
                category=category,
                image=None,
            )
        default_ids = {
            box.pk for box in self.cleaned_data.get('default_for_boxes', [])
        }
        CatalogAssignment.objects.bulk_create([
            CatalogAssignment(
                gift_box=box,
                box_item=self.instance,
                is_default=box.pk in default_ids,
                image=None,
            )
            for box in self.cleaned_data.get('gift_boxes', [])
        ])
        CatalogAssignment.objects.bulk_create([
            CatalogAssignment(
                box_item=self.instance,
                event=event,
            )
            for event in self.cleaned_data.get('events', [])
        ])
        CatalogAssignment.objects.filter(
            box_item=self.instance,
            image__isnull=False,
            pk__in=self.cleaned_data.get('remove_images', []),
        ).delete()
        CatalogAssignment.objects.bulk_create([
            CatalogAssignment(box_item=self.instance, image=image)
            for image in self.cleaned_data.get('images', [])
        ])

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is None or price <= 0:
            raise ValidationError('Product price must be greater than zero.')
        return price

    def clean_stock(self):
        stock = self.cleaned_data.get('stock')
        if stock is None or stock < 0:
            raise ValidationError('Stock quantity cannot be negative.')
        return stock


class AdminOrderUpdateForm(forms.Form):
    order_status = forms.ChoiceField(
        choices=Order.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select rounded-pill'}),
    )
    payment_status = forms.ChoiceField(
        choices=Order.PAYMENT_STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select rounded-pill'}),
    )
    tracking_number = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control form-control-custom'}),
    )
    internal_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control form-control-custom',
            'rows': 3,
            'placeholder': 'Add packaging notes or fulfillment records...',
        }),
    )
    delivery_date = forms.DateField(
        required=False,
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(attrs={'class': 'form-control form-control-custom', 'type': 'date'}),
    )
