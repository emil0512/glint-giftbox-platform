import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.admin.models import LogEntry
from django.utils import timezone
from django.core import mail
from django.db import connection
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.core.management import call_command
from datetime import timedelta

from core.forms import BoxItemForm
from core.models import (
    UserProfile, EventOccasion, Category, GiftBox, BoxItem, CatalogAssignment,
    BoxConfiguration, BoxSelection, Order
)
from django.apps import apps


class GlintPlatformTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME='127.0.0.1')

        self.occasion = EventOccasion.objects.create(name='Birthday')

        self.box = GiftBox.objects.create(
            name='Birthday Bliss',
            event=self.occasion,
            base_price=Decimal('499.00'),
            stock=20
        )

        self.item1 = BoxItem.objects.create(
            name='Artisan Cake',
            price=Decimal('199.00'),
            stock=50
        )
        category, _ = Category.objects.get_or_create(name='Chocolates')
        CatalogAssignment.objects.create(box_item=self.item1, category=category)
        CatalogAssignment.objects.create(
            gift_box=self.box, box_item=self.item1, is_default=True,
        )

        self.item2 = BoxItem.objects.create(
            name='Helium Balloons',
            price=Decimal('99.00'),
            stock=50
        )
        CatalogAssignment.objects.create(box_item=self.item2, category=category)
        CatalogAssignment.objects.create(gift_box=self.box, box_item=self.item2)

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@example.com',
            password='Password123'
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            phone='9876543210',
            city='Bengaluru'
        )

        self.admin_user = User.objects.create_user(
            username='adminuser',
            email='admin@example.com',
            password='Password123',
            is_staff=True
        )

    def test_home_page_renders_successfully(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'GLINT')
        self.assertEqual(len(list(apps.get_app_config('core').get_models())), 9)
        core_tables = [
            table for table in connection.introspection.table_names()
            if table.startswith('core_')
        ]
        self.assertEqual(len(core_tables), 9)

    def test_event_recommendations_appear_in_catalog_and_builder(self):
        recommended = BoxItem.objects.create(
            name='Occasion-only Candle',
            price=Decimal('75.00'),
            stock=10,
        )
        CatalogAssignment.objects.create(
            box_item=recommended,
            category=self.item1.categories.first(),
        )
        CatalogAssignment.objects.create(box_item=recommended, event=self.occasion)

        response = self.client.get(reverse('gift_boxes_by_event', args=[self.occasion.pk]))
        self.assertContains(response, self.box.name)
        self.assertContains(response, recommended.name)
        response = self.client.get(
            reverse('gift_boxes_by_event', args=[self.occasion.pk]),
            {'category': 'Chocolates'},
        )
        self.assertContains(response, recommended.name)
        response = self.client.get(reverse('customizer_builder', args=[self.box.pk]))
        self.assertContains(response, recommended.name)

        response = self.client.post(reverse('add_to_cart', args=[self.box.pk]), {
            'personalization_name': 'To Ananya',
            'selected_items_json': json.dumps([{'id': recommended.pk, 'qty': 1}]),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            BoxSelection.objects.get(configuration__status=BoxConfiguration.CART).box_item,
            recommended,
        )

    def test_catalog_search_and_filter(self):
        # Filter by occasion id
        response = self.client.get(reverse('gift_boxes_by_event', args=[self.occasion.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Birthday Bliss')

        # Search query
        response = self.client.get(reverse('search_gift_boxes') + '?query=Birthday')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Birthday Bliss')

    def test_customizer_builder_page(self):
        response = self.client.get(reverse('customizer_builder', args=[self.box.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Artisan Cake')

    def test_guest_draft_can_be_saved_and_resumed(self):
        response = self.client.post(
            reverse('save_draft_api'),
            data=json.dumps({
                'box_id': self.box.pk,
                'draft_name': 'Birthday idea',
                'items': [{'id': self.item1.pk, 'qty': 2}],
                'personalization_name': 'To Ananya',
                'personalization_message': 'Best wishes',
                'special_instructions': 'Use blue ribbon',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        draft_id = response.json()['draft_id']
        draft = BoxConfiguration.objects.get(pk=draft_id)
        self.assertEqual(draft.status, BoxConfiguration.DRAFT)
        self.assertEqual(draft.draft_name, 'Birthday idea')
        self.assertEqual(draft.special_instructions, 'Use blue ribbon')
        self.assertEqual(draft.selections.get().quantity, 2)

        response = self.client.get(
            reverse('customizer_builder', args=[self.box.pk]) + f'?draft_id={draft_id}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'To Ananya')

    def test_box_item_default_and_available_links_share_normalized_relation(self):
        self.assertEqual(list(self.box.default_items), [self.item1])
        self.assertEqual(self.box.available_items.count(), 2)

        form = BoxItemForm(data={
            'name': self.item2.name,
            'categories': 'Chocolates, Confectionery',
            'price': str(self.item2.price),
            'stock': str(self.item2.stock),
            'is_active': 'on',
            'gift_boxes': [str(self.box.pk)],
            'default_for_boxes': [str(self.box.pk)],
            'events': [str(self.occasion.pk)],
        }, instance=self.item2)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertTrue(self.box.default_items.filter(pk=self.item2.pk).exists())
        self.assertEqual(
            set(self.item2.categories.values_list('name', flat=True)),
            {'Chocolates', 'Confectionery'},
        )
        self.assertEqual(list(self.item2.events), [self.occasion])

    def test_catalog_entity_keys_and_selection_keys_are_enforced(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EventOccasion.objects.create(name='Birthday')

        configuration = BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.CART,
        )
        BoxSelection.objects.create(
            configuration=configuration,
            box_item=self.item1,
            item_name_at_order=self.item1.name,
            unit_price_at_order=self.item1.price,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BoxSelection.objects.create(
                    configuration=configuration,
                    box_item=self.item1,
                    item_name_at_order=self.item1.name,
                    unit_price_at_order=self.item1.price,
                )

    def test_order_total_uses_saved_transaction_price_snapshot(self):
        order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
        )
        configuration = BoxConfiguration.objects.create(
            user=self.user,
            order=order,
            gift_box=self.box,
            status=BoxConfiguration.ORDERED,
            box_name_at_order=self.box.name,
            base_price_at_order=Decimal('699.00'),
            unit_price_at_order=Decimal('998.00'),
        )
        BoxSelection.objects.create(
            configuration=configuration,
            box_item=self.item1,
            item_name_at_order=self.item1.name,
            unit_price_at_order=Decimal('199.00'),
        )

        self.assertEqual(order.subtotal, Decimal('998.00'))

    def test_add_to_cart_with_mandatory_recipient_validation(self):
        self.client.login(username='testuser', password='Password123')

        payload = {
            'personalization_name': '',
            'selected_items_json': '[{\"id\": %d, \"qty\": 1}]' % self.item1.id
        }
        response = self.client.post(
            reverse('add_to_cart', args=[self.box.id]),
            data=payload
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BoxConfiguration.objects.filter(status=BoxConfiguration.CART).count(), 0)

        payload = {
            'personalization_name': 'To Ananya',
            'personalization_message': 'Happy Birthday!',
            'selected_items_json': '[{\"id\": %d, \"qty\": 2}]' % self.item1.id
        }
        response = self.client.post(
            reverse('add_to_cart', args=[self.box.id]),
            data=payload
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BoxConfiguration.objects.filter(status=BoxConfiguration.CART).count(), 1)

        cart_item = BoxConfiguration.objects.get(status=BoxConfiguration.CART)
        # Price: 499 + (199 * 2) = 897
        self.assertEqual(cart_item.get_unit_price(), Decimal('897.00'))
        self.assertEqual(cart_item.personalization_name, 'To Ananya')
        self.assertContains(self.client.get(reverse('cart')), 'Artisan Cake')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_checkout_and_atomic_order_processing(self):
        self.client.login(username='testuser', password='Password123')

        cart_item = BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.CART,
            personalization_name='Aarav',
            quantity=1
        )
        BoxSelection.objects.create(
            configuration=cart_item,
            box_item=self.item1,
            item_name_at_order=self.item1.name,
            unit_price_at_order=self.item1.price,
            quantity=1,
        )

        initial_stock = self.box.stock
        initial_item_stock = self.item1.stock

        checkout_payload = {
            'full_name': 'Test Customer',
            'email': 'test@example.com',
            'phone': '9876543210',
            'shipping_address': '123 Rose Street',
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'postal_code': '560001',
            'payment_method': 'card',
            'delivery_date': str(timezone.localdate() + timedelta(days=1)),
        }
        response = self.client.post(reverse('checkout'), data=checkout_payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('payment'))

        response = self.client.post(reverse('payment'), data={'result': 'declined'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.box.stock, initial_stock)

        response = self.client.post(reverse('payment'), data={'result': 'approved'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(
            BoxConfiguration.objects.filter(status=BoxConfiguration.CART).count(), 0,
        )

        order = Order.objects.first()
        self.assertTrue(order.order_number.startswith('GLINT-'))
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.delivery_date, timezone.localdate() + timedelta(days=1))
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.subtotal, Decimal('698.00'))
        self.assertEqual(order.grand_total, Decimal('782.90'))
        order_item = order.items.first()
        self.assertEqual(order_item.selections.count(), 1)
        self.assertEqual(order_item.total_price, Decimal('698.00'))
        self.assertEqual(order_item.unit_price_snapshot, Decimal('698.00'))
        self.assertEqual(order_item.selections.first().item_name, self.item1.name)
        self.assertEqual(order_item.selections.first().unit_price, self.item1.price)
        self.item1.price = Decimal('299.00')
        self.item1.save(update_fields=['price'])
        self.box.base_price = Decimal('599.00')
        self.box.save(update_fields=['base_price'])
        order_item.refresh_from_db()
        self.assertEqual(order_item.total_price, Decimal('698.00'))
        self.assertEqual(order_item.unit_price_snapshot, Decimal('698.00'))
        self.assertContains(
            self.client.get(reverse('order_confirmation', args=[order.order_number])),
            self.item1.name,
        )
        self.assertContains(
            self.client.get(reverse('order_detail', args=[order.order_number])),
            self.item1.name,
        )

        self.box.refresh_from_db()
        self.assertEqual(self.box.stock, initial_stock - 1)
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.stock, initial_item_stock - 1)
        response = self.client.post(reverse('cancel_order', args=[order.order_number]))
        self.assertEqual(response.status_code, 302)
        self.box.refresh_from_db()
        self.item1.refresh_from_db()
        self.assertEqual(self.box.stock, initial_stock)
        self.assertEqual(self.item1.stock, initial_item_stock)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_registration_requires_email_activation(self):
        response = self.client.post(reverse('register'), {
            'full_name': 'New Customer',
            'email': 'new-customer@example.com',
            'password': 'Password123!',
            'password_confirm': 'Password123!',
            'terms_agreed': 'on',
        })
        self.assertRedirects(response, reverse('login'))
        user = User.objects.get(email='new-customer@example.com')
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        activation_url = next(
            line for line in mail.outbox[0].body.splitlines()
            if '/activate/' in line
        )
        response = self.client.get(activation_url)
        self.assertRedirects(response, reverse('login'))
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_payment_rejects_out_of_stock_items_without_creating_order(self):
        self.client.login(username='testuser', password='Password123')
        BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.CART,
            personalization_name='Aarav',
        )
        cart_item = BoxConfiguration.objects.get(status=BoxConfiguration.CART)
        BoxSelection.objects.create(
            configuration=cart_item,
            box_item=self.item1,
            item_name_at_order=self.item1.name,
            unit_price_at_order=self.item1.price,
        )
        self.item1.stock = 0
        self.item1.save(update_fields=['stock'])
        self.client.post(reverse('checkout'), {
            'full_name': 'Test Customer',
            'email': 'test@example.com',
            'phone': '9876543210',
            'shipping_address': '123 Rose Street',
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'postal_code': '560001',
            'payment_method': 'card',
        })
        response = self.client.post(reverse('payment'), {'result': 'approved'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)
        self.assertTrue(BoxConfiguration.objects.filter(status=BoxConfiguration.CART).exists())
    def test_account_lockout_after_five_failed_attempts(self):
        from django.core.cache import cache
        cache.clear()
        
        for i in range(5):
            response = self.client.post(reverse('login'), {'username_or_email': 'testuser', 'password': 'WrongPassword!'})
            if i < 4:
                self.assertContains(response, 'Invalid credentials')
            else:
                self.assertContains(response, 'Account locked!')

        cache.clear()


    def test_admin_dashboard_access_control(self):
        self.client.login(username='testuser', password='Password123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 302)

        self.client.login(username='adminuser', password='Password123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_customer_order_history_can_filter_by_date_and_status(self):
        recent_order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
            order_status='shipped',
        )
        older_order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
            order_status='pending',
        )
        Order.objects.filter(pk=older_order.pk).update(
            created_at=timezone.now() - timedelta(days=10),
        )
        self.client.login(username='testuser', password='Password123')
        response = self.client.get(reverse('profile'), {
            'status': 'shipped',
            'start_date': timezone.localdate().isoformat(),
            'end_date': timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['orders']), [recent_order])

    def test_cart_and_draft_deletions_require_post(self):
        self.client.login(username='testuser', password='Password123')
        draft = BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.DRAFT,
        )
        self.assertEqual(
            self.client.get(reverse('delete_draft', args=[draft.pk])).status_code,
            405,
        )
        self.assertTrue(BoxConfiguration.objects.filter(pk=draft.pk).exists())

    def test_draft_cleanup_removes_only_drafts_older_than_30_days(self):
        stale_draft = BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.DRAFT,
        )
        recent_draft = BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.DRAFT,
        )
        BoxConfiguration.objects.filter(pk=stale_draft.pk).update(
            updated_at=timezone.now() - timedelta(days=31),
        )
        call_command('cleanup_abandoned_drafts', verbosity=0)
        self.assertFalse(BoxConfiguration.objects.filter(pk=stale_draft.pk).exists())
        self.assertTrue(BoxConfiguration.objects.filter(pk=recent_draft.pk).exists())

    def test_admin_can_update_order_tracking_and_reject_invalid_status(self):
        order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
        )
        self.client.login(username='adminuser', password='Password123')
        url = reverse('admin_order_detail', args=[order.order_number])

        response = self.client.post(url, {
            'order_status': 'shipped',
            'payment_status': 'paid',
            'tracking_number': 'TRACK-123',
            'internal_notes': 'Packed with care',
            'delivery_date': '2026-12-25',
        })
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.tracking_number, 'TRACK-123')
        self.assertEqual(order.internal_notes, 'Packed with care')
        self.assertEqual(str(order.delivery_date), '2026-12-25')
        self.assertTrue(
            LogEntry.objects.filter(user=self.admin_user, object_repr__contains=order.order_number).exists()
        )

        response = self.client.post(url, {
            'order_status': 'unknown',
            'payment_status': 'paid',
            'tracking_number': '',
            'internal_notes': '',
            'delivery_date': '',
        })
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.order_status, 'shipped')
