from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from core.models import (
    UserProfile, EventOccasion, Category, GiftBox, BoxItem, CatalogAssignment,
    Order, BoxConfiguration, BoxSelection
)


class Command(BaseCommand):
    help = 'Seeds initial data for Glint Personalized Gift Collection platform (slug-free, minimal tables)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Starting database seed for Glint...'))

        # 1. Admin User
        admin_user, created = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@glintgifts.com',
                'first_name': 'Glint',
                'last_name': 'Administrator',
                'is_staff': True,
                'is_superuser': True,
            }
        )
        if created:
            admin_user.set_password('admin123')
            admin_user.save()
            UserProfile.objects.get_or_create(user=admin_user, defaults={'phone': '9876543210', 'city': 'Mumbai', 'state': 'Maharashtra'})
            self.stdout.write(self.style.SUCCESS('  - Created admin user (admin / admin123)'))

        # 2. Demo Customer User
        customer_user, created = User.objects.get_or_create(
            username='customer',
            defaults={
                'email': 'customer@glintgifts.com',
                'first_name': 'Aarav',
                'last_name': 'Sharma',
                'is_staff': False,
                'is_superuser': False,
            }
        )
        if created:
            customer_user.set_password('password123')
            customer_user.save()
            UserProfile.objects.get_or_create(
                user=customer_user,
                defaults={
                    'phone': '9988776655',
                    'address_line1': 'Flat 402, Lotus Residency, 10th Cross',
                    'city': 'Bengaluru',
                    'state': 'Karnataka',
                    'postal_code': '560038',
                }
            )
            self.stdout.write(self.style.SUCCESS('  - Created demo customer (customer / password123)'))

        # 3. Occasions (name is unique identifier, no slug)
        occasion_names = ['Birthday', 'Anniversary', "Valentine's Day", 'Graduation', 'Baby Shower', 'Christmas & Holidays', "Mother's Day", 'Wedding & Engagement']
        occasion_map = {}
        for name in occasion_names:
            obj, _ = EventOccasion.objects.get_or_create(name=name, defaults={'is_active': True})
            occasion_map[name] = obj
        self.stdout.write(self.style.SUCCESS(f'  - Loaded {len(occasion_map)} Event Occasions'))

        # 4. Gift Boxes & Items Catalog (minimal fields only)
        boxes_data = [
            {
                'name': 'Birthday Bliss Box',
                'event': 'Birthday',
                'base_price': Decimal('499.00'),
                'items': [
                    ('Artisanal Birthday Cake', 'Chocolates', Decimal('199.00'), True),
                    ('Party Helium Balloons (Set of 3)', 'Plush', Decimal('99.00'), True),
                    ('Sparkling Confetti Poppers', 'Plush', Decimal('49.00'), True),
                    ('Embossed Birthday Card', 'Keepsakes', Decimal('49.00'), True),
                    ('Scented Birthday Candles (Set of 4)', 'Self-care', Decimal('103.00'), True),
                    ('Gourmet Macarons (Box of 4)', 'Chocolates', Decimal('149.00'), False),
                ]
            },
            {
                'name': 'Birthday Surprise Celebration',
                'event': 'Birthday',
                'base_price': Decimal('599.00'),
                'items': [
                    ('Belgian Chocolate Cake', 'Chocolates', Decimal('249.00'), True),
                    ('Golden Foil Birthday Banner', 'Plush', Decimal('79.00'), True),
                    ('Festive Party Hat Trio', 'Plush', Decimal('49.00'), True),
                    ('Luxury Ribbon Gift Wrapping', 'Keepsakes', Decimal('89.00'), True),
                ]
            },
            {
                'name': 'Anniversary Love & Romance',
                'event': 'Anniversary',
                'base_price': Decimal('699.00'),
                'items': [
                    ('Fresh Crimson Red Roses Bouquet', 'Flowers', Decimal('299.00'), True),
                    ('Swiss Dark Chocolate Truffles', 'Chocolates', Decimal('199.00'), True),
                    ('Calligraphy Love Letter Scroll', 'Keepsakes', Decimal('99.00'), True),
                    ('Vanilla Sandalwood Soy Candle', 'Self-care', Decimal('102.00'), True),
                ]
            },
            {
                'name': "Valentine's Sweetheart Special",
                'event': "Valentine's Day",
                'base_price': Decimal('799.00'),
                'items': [
                    ('Heart-Shaped Red Velvet Cake', 'Chocolates', Decimal('299.00'), True),
                    ('Hazelnut Cocoa Truffles', 'Chocolates', Decimal('199.00'), True),
                    ('Velvet Red Rose Posy', 'Flowers', Decimal('199.00'), True),
                    ('Handwritten Romantic Note Card', 'Keepsakes', Decimal('102.00'), True),
                ]
            },
            {
                'name': 'Graduation Glory Box',
                'event': 'Graduation',
                'base_price': Decimal('599.00'),
                'items': [
                    ('Hardwood Diploma Display Frame', 'Luxury', Decimal('199.00'), True),
                    ('Executive Metal Pen Set', 'Luxury', Decimal('149.00'), True),
                    ('Inspirational Success Book', 'Keepsakes', Decimal('199.00'), True),
                ]
            },
            {
                'name': 'Baby Shower Little Angel',
                'event': 'Baby Shower',
                'base_price': Decimal('649.00'),
                'items': [
                    ('Organic Cotton Baby Onesie', 'Plush', Decimal('199.00'), True),
                    ('Velvet Plush Bunny Toy', 'Plush', Decimal('199.00'), True),
                    ('Handmade Knitted Booties', 'Plush', Decimal('149.00'), True),
                    ('Welcome Little One Card', 'Keepsakes', Decimal('102.00'), True),
                ]
            },
            {
                'name': 'Christmas Warmth & Cocoa',
                'event': 'Christmas & Holidays',
                'base_price': Decimal('749.00'),
                'items': [
                    ('Glass Christmas Bauble Ornament', 'Keepsakes', Decimal('199.00'), True),
                    ('Spiced Gingerbread Cookies (Tin)', 'Chocolates', Decimal('149.00'), True),
                    ('Gourmet Marshmallow Hot Cocoa Mix', 'Chocolates', Decimal('199.00'), True),
                ]
            },
            {
                'name': "Mom's Serene Spa Day",
                'event': "Mother's Day",
                'base_price': Decimal('699.00'),
                'items': [
                    ('Organic Lavender Essential Oil Diffuser', 'Self-care', Decimal('249.00'), True),
                    ('Rose & Chamomile Bath Bomb Set', 'Self-care', Decimal('149.00'), True),
                    ('Artisanal Herbal Floral Tea Tin', 'Chocolates', Decimal('199.00'), True),
                ]
            },
            {
                'name': 'Wedding Bliss Celebration Box',
                'event': 'Wedding & Engagement',
                'base_price': Decimal('899.00'),
                'items': [
                    ('Engraved Crystal Champagne Flutes (Pair)', 'Luxury', Decimal('349.00'), True),
                    ('Artisan Wedding Praline Box', 'Chocolates', Decimal('249.00'), True),
                    ('Linen Bound Wedding Keepsake Album', 'Keepsakes', Decimal('199.00'), True),
                ]
            },
        ]

        total_boxes = 0
        total_items = 0
        for b_data in boxes_data:
            event_obj = occasion_map[b_data['event']]
            gift_box, _ = GiftBox.objects.update_or_create(
                name=b_data['name'],
                defaults={
                    'event': event_obj,
                    'base_price': b_data['base_price'],
                    'is_active': True,
                    'stock': 40,
                }
            )
            total_boxes += 1
            for item_name, cat_name, item_price, is_default in b_data['items']:
                box_item, _ = BoxItem.objects.get_or_create(
                    name=item_name,
                    defaults={
                        'price': item_price,
                        'stock': 100,
                        'is_active': True,
                    }
                )
                if box_item.price != item_price:
                    box_item.price = item_price
                    box_item.save()
                category, _ = Category.objects.get_or_create(name=cat_name)
                CatalogAssignment.objects.get_or_create(
                    box_item=box_item,
                    category=category,
                )
                CatalogAssignment.objects.update_or_create(
                    box_item=box_item,
                    gift_box=gift_box,
                    defaults={'is_default': is_default, 'category': None, 'image': None},
                )
                total_items += 1

        self.stdout.write(self.style.SUCCESS(f'  - Created {total_boxes} Gift Boxes and {total_items} Box Item associations'))

        # 5. Demo Orders
        sample_box = GiftBox.objects.first()
        if sample_box:
            sample_item = BoxItem.objects.filter(name='Artisanal Birthday Cake').first()
            order1, created = Order.objects.get_or_create(
                order_number='GLINT-2026-DEMO01',
                defaults={
                    'user': customer_user,
                    'contact_full_name': 'Aarav Sharma',
                    'contact_email': 'customer@glintgifts.com',
                    'contact_phone': '9988776655',
                    'ship_to_address': 'Flat 402, Lotus Residency, 10th Cross, Indiranagar',
                    'ship_to_city': 'Bengaluru',
                    'ship_to_state': 'Karnataka',
                    'ship_to_postal_code': '560038',
                    'tax_amount': Decimal('49.90'),
                    'shipping_amount': Decimal('0.00'),
                    'payment_method': 'card',
                    'payment_status': 'paid',
                    'order_status': 'shipped',
                }
            )
            if created:
                configuration = BoxConfiguration.objects.create(
                    order=order1,
                    status=BoxConfiguration.ORDERED,
                    gift_box=sample_box,
                    box_name_at_order=sample_box.name,
                    base_price_at_order=sample_box.base_price,
                    unit_price_at_order=(
                        sample_box.base_price + sample_item.price
                        if sample_item else sample_box.base_price
                    ),
                    personalization_name='To my sister Priya',
                    personalization_message='Happy Birthday dear sis!',
                    quantity=1,
                )
                if sample_item:
                    BoxSelection.objects.create(
                        configuration=configuration,
                        box_item=sample_item,
                        item_name_at_order=sample_item.name,
                        unit_price_at_order=sample_item.price,
                        quantity=1,
                    )
            # Sample saved draft (no draft_name / instructions / calculated_total — stripped)
            sample_cart_item, cart_created = BoxConfiguration.objects.get_or_create(
                user=customer_user,
                gift_box=sample_box,
                personalization_name='Rohan',
                status=BoxConfiguration.DRAFT,
                defaults={
                    'personalization_message': 'Best wishes for the new decade brother!',
                }
            )
            if cart_created and sample_item:
                BoxSelection.objects.create(
                    configuration=sample_cart_item,
                    box_item=sample_item,
                    item_name_at_order=sample_item.name,
                    unit_price_at_order=sample_item.price,
                    quantity=1,
                )

        self.stdout.write(self.style.SUCCESS('Database seed completed successfully!'))
