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

from core.forms import BoxItemForm, CheckoutForm, RegisterForm
from core.models import (
    UserProfile, EventOccasion, Category, GiftBox, BoxItem, CatalogAssignment,
    BoxConfiguration, BoxSelection, Order
)
from django.apps import apps


class GlintPlatformTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME='127.0.0.1')

        self.occasion, _ = EventOccasion.objects.get_or_create(
            name='Birthday',
            defaults={'is_active': True},
        )

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
        self.assertContains(response, 'data-bs-toggle="dropdown"')
        self.assertContains(response, reverse('gift_boxes_by_event', args=[self.occasion.pk]))
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

    def test_event_catalog_filters_preserve_selected_occasion(self):
        second_box = GiftBox.objects.create(
            name='Birthday Treat Box',
            event=self.occasion,
            base_price=Decimal('299.00'),
        )
        CatalogAssignment.objects.create(gift_box=second_box, box_item=self.item2)

        response = self.client.get(
            reverse('gift_boxes_by_event', args=[self.occasion.pk]),
            {'category': 'Chocolates', 'sort': 'price_low', 'query': 'Birthday'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_occasion'], self.occasion)
        self.assertEqual(response.context['selected_category'], 'Chocolates')
        self.assertEqual(response.context['selected_sort'], 'price_low')
        self.assertEqual(response.context['query'], 'Birthday')
        self.assertEqual(list(response.context['gift_boxes']), [second_box, self.box])
        self.assertContains(
            response,
            f'action="{reverse("gift_boxes_by_event", args=[self.occasion.pk])}"',
        )

        # Search query
        response = self.client.get(reverse('search_gift_boxes') + '?query=Birthday')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Birthday Bliss')

    def test_customizer_builder_page(self):
        response = self.client.get(reverse('customizer_builder', args=[self.box.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Artisan Cake')

    def test_shared_ambient_animation_is_rendered_on_main_site_pages(self):
        for url in (
            reverse('home'),
            reverse('gift_boxes'),
            reverse('cart'),
            reverse('login'),
            reverse('register'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="ambient-layer"')
                self.assertContains(response, 'class="butterfly-flutter"')

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
        order = Order.objects.get()
        self.assertRedirects(
            response,
            reverse('order_confirmation', args=[order.order_number]),
        )

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(
            BoxConfiguration.objects.filter(status=BoxConfiguration.CART).count(), 0,
        )

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
    def test_registration_does_not_require_email_activation(self):
        response = self.client.post(reverse('register'), {
            'full_name': 'New Customer',
            'email': 'new-customer@example.com',
            'password': 'Password123!',
            'password_confirm': 'Password123!',
            'terms_agreed': 'on',
        })
        self.assertRedirects(response, reverse('login'))
        user = User.objects.get(email='new-customer@example.com')
        self.assertTrue(user.is_active)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(self.client.login(
            username=user.username,
            password='Password123!',
        ))

    def test_registration_policy_links_open_information_pages(self):
        response = self.client.get(reverse('register'))
        self.assertContains(response, reverse('terms_of_service'))
        self.assertContains(response, reverse('privacy_policy'))
        self.assertContains(response, 'placeholder="Your full name"')
        self.assertNotContains(response, 'e.g.')
        self.assertContains(self.client.get(reverse('terms_of_service')), 'demonstration application')
        self.assertContains(self.client.get(reverse('privacy_policy')), 'Passwords are handled')

    def test_registration_validation_shows_errors_and_preserves_next(self):
        protected_path = reverse('admin_products')
        response = self.client.post(
            reverse('register'),
            {
                'next': protected_path,
                'full_name': 'Test Customer',
                'email': 'invalid-email',
                'password': 'weak',
                'password_confirm': 'different',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next'], protected_path)
        self.assertContains(response, 'You must agree to the Terms')
        self.assertContains(response, 'Enter a valid email address')
        self.assertFalse(User.objects.filter(email='invalid-email').exists())

    def test_checkout_form_rejects_invalid_phone_and_past_delivery_date(self):
        form = CheckoutForm(data={
            'full_name': 'Test Customer',
            'email': 'test@example.com',
            'phone': '123',
            'shipping_address': '123 Rose Street',
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'postal_code': '560001',
            'payment_method': 'card',
            'delivery_date': (timezone.localdate() - timedelta(days=1)).isoformat(),
        })

        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
        self.assertIn('delivery_date', form.errors)

    def test_invalid_profile_form_errors_are_visible_on_profile_tab(self):
        self.client.login(username='testuser', password='Password123')
        response = self.client.post(reverse('profile'), {
            'full_name': 'Test Customer',
            'email': self.user.email,
            'phone': '123',
            'address_line1': '123 Rose Street',
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'postal_code': '560001',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_tab'], 'account-tab')
        self.assertContains(response, 'Phone number must be exactly 10 digits.')
        self.assertContains(response, 'id="account-tab" role="tabpanel"')

    def test_customer_dashboard_shows_active_gift_boxes(self):
        self.client.login(username='testuser', password='Password123')
        inactive_box = GiftBox.objects.create(
            name='Inactive Gift Box',
            event=self.occasion,
            base_price=Decimal('199.00'),
            is_active=False,
        )

        response = self.client.get(reverse('profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Explore Gift Boxes')
        self.assertContains(response, self.box.name)
        self.assertContains(response, reverse('gift_box_detail', args=[self.box.pk]))
        self.assertContains(response, reverse('customizer_builder', args=[self.box.pk]))
        self.assertNotContains(response, inactive_box.name)

    def test_password_reset_handles_unknown_email_without_enumerating_accounts(self):
        response = self.client.post(
            reverse('forgot_password'),
            {'email': 'unknown@example.com'},
            follow=True,
        )
        self.assertEqual(response.redirect_chain[-1][0], reverse('forgot_password'))
        self.assertContains(response, 'If an account exists')
        self.assertNotContains(response, 'No registered account found')
        self.assertNotIn('password_reset_otp', self.client.session)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_password_reset_does_not_claim_email_was_sent_with_console_backend(self):
        response = self.client.post(
            reverse('forgot_password'),
            {'email': self.user.email},
            follow=True,
        )

        self.assertContains(response, 'Email delivery is not configured yet.')
        self.assertNotIn('password_reset_otp', self.client.session)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_otp_completes_password_change(self):
        response = self.client.post(
            reverse('forgot_password'),
            {'email': self.user.email},
        )
        self.assertRedirects(response, reverse('reset_password_otp'))
        self.assertEqual(len(mail.outbox), 1)

        otp = self.client.session['password_reset_otp']
        response = self.client.post(reverse('reset_password_otp'), {
            'otp': otp,
            'new_password': 'NewPassword123!',
            'confirm_password': 'NewPassword123!',
        })
        self.assertRedirects(response, reverse('login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPassword123!'))
        self.assertNotIn('password_reset_otp', self.client.session)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_activates_unactivated_account(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post(
            reverse('forgot_password'),
            {'email': self.user.email},
        )
        self.assertRedirects(response, reverse('reset_password_otp'))
        otp = self.client.session['password_reset_otp']

        response = self.client.post(reverse('reset_password_otp'), {
            'otp': otp,
            'new_password': 'NewPassword123!',
            'confirm_password': 'NewPassword123!',
        })

        self.assertRedirects(response, reverse('login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.check_password('NewPassword123!'))
        self.assertTrue(self.client.login(username='testuser', password='NewPassword123!'))

    def test_unactivated_account_login_explains_email_verification(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post(reverse('login'), {
            'username_or_email': self.user.username,
            'password': 'Password123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'This account is not activated. Use Forgot Password to verify your email and activate it.',
        )
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_password_reset_locks_otp_after_five_incorrect_attempts(self):
        session = self.client.session
        session['password_reset_otp'] = '123456'
        session['password_reset_email'] = self.user.email
        session['password_reset_otp_at'] = timezone.now().isoformat()
        session['password_reset_attempts'] = 0
        session.save()

        for attempt in range(5):
            response = self.client.post(reverse('reset_password_otp'), {
                'otp': '000000',
                'new_password': 'NewPassword123!',
                'confirm_password': 'NewPassword123!',
            })
            if attempt < 4:
                self.assertEqual(response.status_code, 200)
            else:
                self.assertRedirects(response, reverse('forgot_password'))

        self.assertNotIn('password_reset_otp', self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Password123'))

    def test_failed_login_keeps_protected_destination_for_retry(self):
        login_url = reverse('login') + '?next=' + reverse('admin_products')
        response = self.client.post(login_url, {
            'username_or_email': self.admin_user.username,
            'password': 'WrongPassword!',
            'next': reverse('admin_products'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next'], reverse('admin_products'))
        self.assertContains(response, f'value="{reverse("admin_products")}"')

        response = self.client.post(reverse('login'), {
            'username_or_email': self.admin_user.username,
            'password': 'Password123',
            'next': reverse('admin_products'),
        })
        self.assertRedirects(response, reverse('admin_products'))

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
        response = self.client.post(reverse('checkout'), {
            'full_name': 'Test Customer',
            'email': 'test@example.com',
            'phone': '9876543210',
            'shipping_address': '123 Rose Street',
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'postal_code': '560001',
            'payment_method': 'card',
        })
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
        self.assertContains(response, 'class="glint-body admin-body"')
        self.assertContains(response, 'class="container-fluid py-4 px-md-5 admin-portal"')

        response = self.client.get(reverse('admin_products'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="glint-body admin-body"')

    def test_admin_dashboard_charts_use_paid_revenue_in_selected_range(self):
        current_order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
            tax_amount=Decimal('5.00'),
            shipping_amount=Decimal('10.00'),
            payment_status='paid',
        )
        old_order = Order.objects.create(
            user=self.user,
            contact_full_name='Old Customer',
            contact_email='old@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
            tax_amount=Decimal('100.00'),
            payment_status='paid',
        )
        Order.objects.filter(pk=old_order.pk).update(
            created_at=timezone.now() - timedelta(days=60),
        )
        self.client.login(username='adminuser', password='Password123')

        response = self.client.get(reverse('admin_dashboard'), {
            'start_date': timezone.localdate().isoformat(),
            'end_date': timezone.localdate().isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_revenue'], current_order.grand_total)
        self.assertEqual(
            json.loads(response.context['monthly_values_json']),
            [float(current_order.grand_total)],
        )
        self.assertEqual(
            json.loads(response.context['monthly_labels_json']),
            [current_order.created_at.strftime('%b %Y')],
        )

    def test_admin_order_search_filters_real_customer_fields(self):
        order = Order.objects.create(
            user=self.user,
            contact_full_name='Searchable Customer',
            contact_email='searchable@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
        )
        self.client.login(username='adminuser', password='Password123')

        response = self.client.get(reverse('admin_orders'), {'q': 'searchable@example.com'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['orders']), [order])

    def test_admin_can_create_edit_and_delete_boxes_and_items(self):
        self.client.login(username='adminuser', password='Password123')

        box_create_url = reverse('admin_box_create')
        self.assertEqual(self.client.get(box_create_url).status_code, 200)
        response = self.client.post(box_create_url, {
            'name': 'Admin Created Box',
            'event': str(self.occasion.pk),
            'description': 'A test gift box',
            'emoji': '🎁',
            'base_price': '350.00',
            'stock': '8',
            'is_active': 'on',
        })
        self.assertRedirects(response, reverse('admin_products'))
        box = GiftBox.objects.get(name='Admin Created Box')

        box_edit_url = reverse('admin_box_edit', args=[box.pk])
        self.assertEqual(self.client.get(box_edit_url).status_code, 200)
        response = self.client.post(box_edit_url, {
            'name': 'Admin Edited Box',
            'event': str(self.occasion.pk),
            'description': 'An updated test gift box',
            'emoji': '🎁',
            'base_price': '450.00',
            'stock': '9',
            'is_active': 'on',
        })
        self.assertRedirects(response, reverse('admin_products'))
        box.refresh_from_db()
        self.assertEqual(box.name, 'Admin Edited Box')
        self.assertEqual(box.base_price, Decimal('450.00'))

        item_create_url = reverse('admin_box_item_create')
        self.assertEqual(self.client.get(item_create_url).status_code, 200)
        item_payload = {
            'name': 'Admin Created Item',
            'description': 'A test customizable item',
            'categories': 'Keepsakes',
            'price': '75.00',
            'stock': '15',
            'is_active': 'on',
            'gift_boxes': [str(box.pk)],
            'default_for_boxes': [str(box.pk)],
            'events': [str(self.occasion.pk)],
        }
        response = self.client.post(item_create_url, item_payload)
        self.assertRedirects(response, reverse('admin_products'))
        item = BoxItem.objects.get(name='Admin Created Item')

        item_edit_url = reverse('admin_box_item_edit', args=[item.pk])
        self.assertEqual(self.client.get(item_edit_url).status_code, 200)
        item_payload.update({'name': 'Admin Edited Item', 'price': '85.00'})
        response = self.client.post(item_edit_url, item_payload)
        self.assertRedirects(response, reverse('admin_products'))
        item.refresh_from_db()
        self.assertEqual(item.name, 'Admin Edited Item')
        self.assertEqual(item.price, Decimal('85.00'))

        response = self.client.post(reverse('admin_box_item_delete', args=[item.pk]))
        self.assertRedirects(response, reverse('admin_products'))
        self.assertFalse(BoxItem.objects.filter(pk=item.pk).exists())
        response = self.client.post(reverse('admin_box_delete', args=[box.pk]))
        self.assertRedirects(response, reverse('admin_products'))
        self.assertFalse(GiftBox.objects.filter(pk=box.pk).exists())

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

    def test_customer_can_rename_and_delete_saved_draft(self):
        self.client.login(username='testuser', password='Password123')
        draft = BoxConfiguration.objects.create(
            user=self.user,
            gift_box=self.box,
            status=BoxConfiguration.DRAFT,
            draft_name='Initial name',
        )

        response = self.client.post(
            reverse('rename_draft', args=[draft.pk]),
            {'draft_name': 'Updated name'},
        )
        self.assertRedirects(response, reverse('profile'))
        draft.refresh_from_db()
        self.assertEqual(draft.draft_name, 'Updated name')

        response = self.client.post(reverse('delete_draft', args=[draft.pk]))
        self.assertRedirects(response, reverse('profile'))
        self.assertFalse(BoxConfiguration.objects.filter(pk=draft.pk).exists())

    def test_guest_can_update_and_remove_cart_item(self):
        self.client.get(reverse('cart'))
        cart_item = BoxConfiguration.objects.create(
            session_key=self.client.session.session_key,
            gift_box=self.box,
            status=BoxConfiguration.CART,
            personalization_name='For a friend',
        )
        BoxSelection.objects.create(
            configuration=cart_item,
            box_item=self.item1,
            item_name_at_order=self.item1.name,
            quantity=1,
            unit_price_at_order=self.item1.price,
        )

        response = self.client.post(
            reverse('update_cart_quantity', args=[cart_item.pk, 'increase']),
        )
        self.assertRedirects(response, reverse('cart'))
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 2)

        response = self.client.post(
            reverse('update_cart_quantity', args=[cart_item.pk, 'decrease']),
        )
        self.assertRedirects(response, reverse('cart'))
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 1)

        response = self.client.post(reverse('remove_from_cart', args=[cart_item.pk]))
        self.assertRedirects(response, reverse('cart'))
        self.assertFalse(BoxConfiguration.objects.filter(pk=cart_item.pk).exists())

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
        BoxConfiguration.objects.create(
            order=order,
            gift_box=self.box,
            status=BoxConfiguration.ORDERED,
            personalization_name='Gift Recipient',
            special_instructions='Handle with care',
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
        packing_slip = self.client.get(
            reverse('admin_packing_slip', args=[order.order_number])
        )
        self.assertEqual(packing_slip.status_code, 200)
        self.assertContains(packing_slip, '2026-12-25')
        self.assertContains(packing_slip, 'Handle with care')
        self.assertContains(packing_slip, 'Packed with care')

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

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_tracking_only_update_emails_customer_and_is_shown_on_invoice(self):
        order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
            order_status='shipped',
            payment_status='paid',
        )
        self.client.login(username='adminuser', password='Password123')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('admin_order_detail', args=[order.order_number]),
                {
                    'order_status': 'shipped',
                    'payment_status': 'paid',
                    'tracking_number': 'COURIER-456',
                    'internal_notes': '',
                    'delivery_date': '',
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('COURIER-456', mail.outbox[0].body)

        self.client.login(username='testuser', password='Password123')
        response = self.client.get(reverse('order_detail', args=[order.order_number]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'COURIER-456')
        self.assertContains(response, 'Order Invoice')
        self.assertContains(response, '@media print')

    def test_customer_can_download_order_invoice_as_pdf(self):
        order = Order.objects.create(
            user=self.user,
            contact_full_name='Test Customer',
            contact_email='test@example.com',
            contact_phone='9876543210',
            ship_to_address='123 Rose Street',
            ship_to_city='Bengaluru',
            ship_to_state='Karnataka',
            ship_to_postal_code='560001',
            tax_amount=Decimal('34.90'),
            shipping_amount=Decimal('0.00'),
            order_status='paid',
            payment_status='paid',
        )
        configuration = BoxConfiguration.objects.create(
            order=order,
            gift_box=self.box,
            status=BoxConfiguration.ORDERED,
            personalization_name='Gift Recipient',
            personalization_message='Happy Birthday',
            quantity=1,
            unit_price_at_order=Decimal('698.00'),
        )
        BoxSelection.objects.create(
            configuration=configuration,
            box_item=self.item1,
            item_name_at_order=self.item1.name,
            unit_price_at_order=self.item1.price,
            quantity=1,
        )
        self.client.login(username='testuser', password='Password123')

        response = self.client.get(reverse('order_invoice_pdf', args=[order.order_number]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(
            f'attachment; filename="invoice-{order.order_number}.pdf"',
            response['Content-Disposition'],
        )
        self.assertTrue(response.content.startswith(b'%PDF-'))

        detail_response = self.client.get(reverse('order_detail', args=[order.order_number]))
        self.assertContains(
            detail_response,
            reverse('order_invoice_pdf', args=[order.order_number]),
        )

        other_user = User.objects.create_user(
            username='othercustomer',
            email='other@example.com',
            password='Password123',
        )
        self.client.login(username=other_user.username, password='Password123')
        unauthorized_response = self.client.get(
            reverse('order_invoice_pdf', args=[order.order_number]),
        )
        self.assertRedirects(unauthorized_response, reverse('profile'))
