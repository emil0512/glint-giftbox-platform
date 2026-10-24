import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class NullableImageField(models.ImageField):
    def get_db_prep_save(self, value, connection):
        if self.null and value is not None and not value:
            value = None
        return super().get_db_prep_save(value, connection)

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=25, blank=True, default='')
    address_line1 = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    postal_code = models.CharField(max_length=20, blank=True, default='')

    def __str__(self):
        return f"{self.user.username}'s Profile"


class EventOccasion(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    emoji = models.CharField(max_length=16, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class GiftBox(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    image = models.ImageField(upload_to='gift_boxes/', null=True, blank=True, default=None)
    emoji = models.CharField(max_length=16, blank=True, default='')
    event = models.ForeignKey(EventOccasion, on_delete=models.CASCADE, related_name='gift_boxes')
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('499.00'))
    is_active = models.BooleanField(default=True)
    stock = models.PositiveIntegerField(default=50)

    class Meta:
        ordering = ['name']

    @property
    def default_items(self):
        return BoxItem.objects.filter(
            catalog_assignments__gift_box=self,
            catalog_assignments__is_default=True,
        )

    def get_default_total(self):
        """Total = base_price + sum of default items' prices (qty 1 each)."""
        total = self.base_price
        for item in self.default_items.filter(is_active=True):
            total += item.price
        return total

    @property
    def image_url(self):
        return self.image.url if self.image else ''

    def __str__(self):
        return f"{self.name} ({self.event.name})"


class BoxItem(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, default='')
    gift_boxes = models.ManyToManyField(
        GiftBox, through='CatalogAssignment', related_name='available_items', blank=True
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    @property
    def categories(self):
        return Category.objects.filter(catalog_assignments__box_item=self)

    @property
    def images(self):
        return self.catalog_assignments.filter(image__isnull=False).exclude(image='')

    @property
    def events(self):
        return EventOccasion.objects.filter(catalog_assignments__box_item=self)

    @property
    def category_name(self):
        return ', '.join(self.categories.values_list('name', flat=True))

    def __str__(self):
        return f"{self.name} (₹{self.price})"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CatalogAssignment(models.Model):
    box_item = models.ForeignKey(BoxItem, on_delete=models.CASCADE, related_name='catalog_assignments')
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.CASCADE,
        related_name='catalog_assignments',
    )
    gift_box = models.ForeignKey(
        GiftBox, null=True, blank=True, on_delete=models.CASCADE,
        related_name='catalog_assignments',
    )
    event = models.ForeignKey(
        EventOccasion, null=True, blank=True, on_delete=models.CASCADE,
        related_name='catalog_assignments',
    )
    image = NullableImageField(
        upload_to='products/gallery/', null=True, blank=True, default=None,
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(category__isnull=False, gift_box__isnull=True, event__isnull=True, image__isnull=True, is_default=False)
                    | models.Q(category__isnull=True, gift_box__isnull=False, event__isnull=True, image__isnull=True)
                    | models.Q(category__isnull=True, gift_box__isnull=True, event__isnull=True, image__isnull=False, is_default=False)
                    | models.Q(category__isnull=True, gift_box__isnull=True, event__isnull=False, image__isnull=True, is_default=False)
                ),
                name='catalog_assignment_has_one_target',
            ),
            models.UniqueConstraint(
                fields=['box_item', 'category'],
                condition=models.Q(category__isnull=False),
                name='unique_box_item_category',
            ),
            models.UniqueConstraint(
                fields=['box_item', 'gift_box'],
                condition=models.Q(gift_box__isnull=False),
                name='unique_box_item_gift_box',
            ),
            models.UniqueConstraint(
                fields=['box_item', 'event'],
                condition=models.Q(event__isnull=False),
                name='unique_box_item_event',
            ),
            models.UniqueConstraint(
                fields=['box_item', 'image'],
                condition=models.Q(image__isnull=False),
                name='unique_box_item_image',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.image:
            self.image = None
        super().save(*args, **kwargs)


class BoxConfiguration(models.Model):
    DRAFT = 'draft'
    CART = 'cart'
    ORDERED = 'ordered'
    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (CART, 'Cart'),
        (ORDERED, 'Ordered'),
    ]

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name='box_configurations')
    session_key = models.CharField(max_length=100, null=True, blank=True)
    gift_box = models.ForeignKey(GiftBox, null=True, blank=True, on_delete=models.SET_NULL)
    order = models.ForeignKey(
        'Order', null=True, blank=True, on_delete=models.CASCADE, related_name='configurations'
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=CART)
    draft_name = models.CharField(max_length=150, blank=True, default='')
    personalization_name = models.CharField(max_length=200, blank=True, default='')
    personalization_message = models.TextField(blank=True, default='')
    special_instructions = models.TextField(blank=True, default='')
    quantity = models.PositiveIntegerField(default=1)
    box_name_at_order = models.CharField(max_length=200, blank=True, default='')
    base_price_at_order = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit_price_at_order = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(status='ordered', order__isnull=False, gift_box__isnull=True)
                    | models.Q(status='ordered', order__isnull=False, gift_box__isnull=False)
                    | models.Q(status__in=['draft', 'cart'], order__isnull=True, gift_box__isnull=False)
                ),
                name='configuration_state_consistent',
            ),
        ]

    @property
    def box_name(self):
        if self.box_name_at_order:
            return self.box_name_at_order
        return self.gift_box.name if self.gift_box_id else ''

    @property
    def base_price(self):
        if self.status == self.ORDERED and self.base_price_at_order is not None:
            return self.base_price_at_order
        return self.gift_box.base_price if self.gift_box_id else Decimal('0.00')

    @property
    def unit_price_snapshot(self):
        return self.unit_price_at_order

    def get_unit_price(self):
        if self.status == self.ORDERED and self.unit_price_at_order is not None:
            return self.unit_price_at_order
        total = self.base_price
        for item in self.selections.all():
            total += item.unit_price * item.quantity
        return total

    def freeze(self):
        if self.gift_box_id:
            self.box_name_at_order = self.gift_box.name
            self.base_price_at_order = self.gift_box.base_price
        self.unit_price_at_order = self.get_unit_price()

    def get_total_price(self):
        return self.get_unit_price() * self.quantity

    @property
    def unit_price(self):
        return self.get_unit_price()

    @property
    def total_price(self):
        return self.get_total_price()

    @property
    def calculated_total(self):
        return self.get_total_price()

    def __str__(self):
        name = self.box_name or (self.gift_box.name if self.gift_box_id else 'Gift Box')
        return f"{self.quantity}x {name}"


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('shipped', 'Shipped'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Test payment completed (no charge)'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('card', 'Credit / Debit Card'),
        ('upi', 'UPI / QR Code'),
        ('netbanking', 'Net Banking'),
        ('cod', 'Cash on Delivery'),
    ]

    order_number = models.CharField(max_length=60, unique=True, editable=False)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    contact_full_name = models.CharField(max_length=150)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30)
    ship_to_address = models.TextField()
    ship_to_city = models.CharField(max_length=100, default='')
    ship_to_state = models.CharField(max_length=100, default='')
    ship_to_postal_code = models.CharField(max_length=20, default='')
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tracking_number = models.CharField(max_length=100, blank=True, default='')
    delivery_date = models.DateField(null=True, blank=True)
    internal_notes = models.TextField(blank=True, default='')
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default='card')
    payment_status = models.CharField(max_length=30, choices=PAYMENT_STATUS_CHOICES, default='paid')
    order_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='paid')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def full_name(self):
        return self.contact_full_name

    @property
    def email(self):
        return self.contact_email

    @property
    def phone(self):
        return self.contact_phone

    @property
    def shipping_address(self):
        return self.ship_to_address

    @property
    def city(self):
        return self.ship_to_city

    @property
    def state(self):
        return self.ship_to_state

    @property
    def postal_code(self):
        return self.ship_to_postal_code

    @property
    def subtotal(self):
        return sum((item.get_total_price() for item in self.configurations.all()), Decimal('0.00'))

    @property
    def items(self):
        return self.configurations

    @property
    def grand_total(self):
        return self.subtotal + self.tax_amount + self.shipping_amount

    def save(self, *args, **kwargs):
        if not self.order_number:
            uid = uuid.uuid4().hex[:8].upper()
            year = timezone.now().strftime('%Y')
            self.order_number = f"GLINT-{year}-{uid}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.order_number} ({self.get_order_status_display()})"


class BoxSelection(models.Model):
    configuration = models.ForeignKey(
        BoxConfiguration, null=True, on_delete=models.CASCADE, related_name='selections'
    )
    box_item = models.ForeignKey(BoxItem, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    item_name_at_order = models.CharField(max_length=150, blank=True, default='')
    unit_price_at_order = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(configuration__isnull=False),
                name='box_selection_configuration_required',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='box_selection_quantity_positive',
            ),
            models.UniqueConstraint(
                fields=['configuration', 'box_item'],
                condition=models.Q(box_item__isnull=False),
                name='unique_selection_item_per_configuration',
            ),
        ]

    @property
    def item_name(self):
        if self.item_name_at_order:
            return self.item_name_at_order
        return self.box_item.name if self.box_item_id else ''

    @property
    def unit_price(self):
        if self.unit_price_at_order is not None:
            return self.unit_price_at_order
        return self.box_item.price if self.box_item_id else Decimal('0.00')

    def freeze(self):
        if self.box_item_id:
            self.item_name_at_order = self.box_item.name
            self.unit_price_at_order = self.box_item.price

    @property
    def qty(self):
        return self.quantity

    @property
    def price(self):
        return self.unit_price
