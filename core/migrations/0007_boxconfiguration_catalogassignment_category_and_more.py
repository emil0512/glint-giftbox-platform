import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migrate_data(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    BoxConfiguration = apps.get_model('core', 'BoxConfiguration')
    BoxItem = apps.get_model('core', 'BoxItem')
    BoxSelection = apps.get_model('core', 'BoxSelection')
    CartItem = apps.get_model('core', 'CartItem')
    CatalogAssignment = apps.get_model('core', 'CatalogAssignment')
    Category = apps.get_model('core', 'Category')
    GiftBoxItem = apps.get_model('core', 'GiftBoxItem')
    OrderItem = apps.get_model('core', 'OrderItem')

    for box_item in BoxItem.objects.using(db_alias).all().iterator():
        category_names = {
            name.strip()
            for name in box_item.category_name.split(',')
            if name.strip()
        } or {'General'}
        for category_name in category_names:
            category, _ = Category.objects.using(db_alias).get_or_create(name=category_name)
            CatalogAssignment.objects.using(db_alias).create(
                box_item_id=box_item.pk,
                category_id=category.pk,
                image=None,
            )

    CatalogAssignment.objects.using(db_alias).bulk_create([
        CatalogAssignment(
            box_item_id=assignment.box_item_id,
            gift_box_id=assignment.gift_box_id,
            image=None,
            is_default=assignment.is_default,
        )
        for assignment in GiftBoxItem.objects.using(db_alias).all().iterator()
    ])

    for assignment in CatalogAssignment.objects.using(db_alias).all().iterator():
        image = assignment.image
        if assignment.category_id is not None:
            valid = (
                assignment.gift_box_id is None
                and image in (None, '')
                and not assignment.is_default
            )
        elif assignment.gift_box_id is not None:
            valid = image in (None, '')
        else:
            valid = image not in (None, '') and not assignment.is_default
        if not valid:
            raise RuntimeError(
                f'Invalid catalog assignment {assignment.pk}: '
                f'category={assignment.category_id}, gift_box={assignment.gift_box_id}, '
                f'image={image!r}, default={assignment.is_default}.'
            )

    table_name = schema_editor.quote_name(CatalogAssignment._meta.db_table)
    image_column = schema_editor.quote_name('image')
    schema_editor.execute(
        f'UPDATE {table_name} SET {image_column} = NULL WHERE {image_column} = %s',
        [''],
    )

    configuration_ids = {}
    for cart_item in CartItem.objects.using(db_alias).all().iterator():
        configuration = BoxConfiguration.objects.using(db_alias).create(
            user_id=cart_item.user_id,
            session_key=cart_item.session_key,
            gift_box_id=cart_item.gift_box_id,
            status='cart',
            personalization_name=cart_item.personalization_name,
            personalization_message=cart_item.personalization_message,
            quantity=cart_item.quantity,
        )
        configuration_ids[('cart', cart_item.pk)] = configuration.pk

    for order_item in OrderItem.objects.using(db_alias).all().iterator():
        configuration = BoxConfiguration.objects.using(db_alias).create(
            user_id=order_item.order.user_id,
            gift_box_id=order_item.gift_box_id,
            order_id=order_item.order_id,
            status='ordered',
            personalization_name=order_item.personalization_name,
            personalization_message=order_item.personalization_message,
            quantity=order_item.quantity,
            box_name=order_item.box_name,
            base_price=order_item.base_price,
            unit_price_snapshot=order_item.unit_price,
        )
        configuration_ids[('order', order_item.pk)] = configuration.pk

    for selection in BoxSelection.objects.using(db_alias).all().iterator():
        if selection.cart_item_id is not None and selection.order_item_id is None:
            key = ('cart', selection.cart_item_id)
        elif selection.order_item_id is not None and selection.cart_item_id is None:
            key = ('order', selection.order_item_id)
        else:
            raise RuntimeError(
                f'Box selection {selection.pk} must belong to exactly one old cart or order item.'
            )
        configuration_id = configuration_ids.get(key)
        if configuration_id is None:
            raise RuntimeError(f'Box selection {selection.pk} has no matching configuration.')
        selection.configuration_id = configuration_id
        selection.save(using=db_alias, update_fields=['configuration'])


def restore_data(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    BoxConfiguration = apps.get_model('core', 'BoxConfiguration')
    BoxItem = apps.get_model('core', 'BoxItem')
    BoxSelection = apps.get_model('core', 'BoxSelection')
    CartItem = apps.get_model('core', 'CartItem')
    CatalogAssignment = apps.get_model('core', 'CatalogAssignment')
    GiftBoxItem = apps.get_model('core', 'GiftBoxItem')
    OrderItem = apps.get_model('core', 'OrderItem')

    for assignment in CatalogAssignment.objects.using(db_alias).filter(
        gift_box__isnull=False
    ).iterator():
        GiftBoxItem.objects.using(db_alias).create(
            box_item_id=assignment.box_item_id,
            gift_box_id=assignment.gift_box_id,
            is_default=assignment.is_default,
        )

    for box_item in BoxItem.objects.using(db_alias).all().iterator():
        category_names = CatalogAssignment.objects.using(db_alias).filter(
            box_item_id=box_item.pk,
            category__isnull=False,
        ).values_list('category__name', flat=True)
        box_item.category_name = ', '.join(category_names) or 'General'
        box_item.save(using=db_alias, update_fields=['category_name'])

    configuration_ids = {}
    for configuration in BoxConfiguration.objects.using(db_alias).all().iterator():
        if configuration.status == 'ordered':
            old_item = OrderItem.objects.using(db_alias).create(
                order_id=configuration.order_id,
                gift_box_id=configuration.gift_box_id,
                box_name=configuration.box_name,
                base_price=configuration.base_price or 0,
                personalization_name=configuration.personalization_name,
                personalization_message=configuration.personalization_message,
                quantity=configuration.quantity,
                unit_price=configuration.unit_price_snapshot or 0,
            )
            configuration_ids[configuration.pk] = ('order', old_item.pk)
        else:
            old_item = CartItem.objects.using(db_alias).create(
                user_id=configuration.user_id,
                session_key=configuration.session_key,
                gift_box_id=configuration.gift_box_id,
                personalization_name=configuration.personalization_name,
                personalization_message=configuration.personalization_message,
                quantity=configuration.quantity,
            )
            configuration_ids[configuration.pk] = ('cart', old_item.pk)

    for selection in BoxSelection.objects.using(db_alias).all().iterator():
        parent = configuration_ids.get(selection.configuration_id)
        if parent is None:
            raise RuntimeError(f'Box selection {selection.pk} has no matching old configuration.')
        if parent[0] == 'cart':
            selection.cart_item_id = parent[1]
        else:
            selection.order_item_id = parent[1]
        selection.save(using=db_alias, update_fields=['cart_item', 'order_item'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_derive_order_totals'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BoxConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, max_length=100, null=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('cart', 'Cart'), ('ordered', 'Ordered')], default='cart', max_length=10)),
                ('draft_name', models.CharField(blank=True, default='', max_length=150)),
                ('personalization_name', models.CharField(blank=True, default='', max_length=200)),
                ('personalization_message', models.TextField(blank=True, default='')),
                ('special_instructions', models.TextField(blank=True, default='')),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('box_name', models.CharField(blank=True, default='', max_length=200)),
                ('base_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('unit_price_snapshot', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='CatalogAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(blank=True, default=None, null=True, upload_to='products/gallery/')),
                ('is_default', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='boxconfiguration',
            name='gift_box',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.giftbox'),
        ),
        migrations.AddField(
            model_name='boxconfiguration',
            name='order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='configurations', to='core.order'),
        ),
        migrations.AddField(
            model_name='boxconfiguration',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='box_configurations', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='catalogassignment',
            name='box_item',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='catalog_assignments', to='core.boxitem'),
        ),
        migrations.AddField(
            model_name='catalogassignment',
            name='gift_box',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='catalog_assignments', to='core.giftbox'),
        ),
        migrations.AddField(
            model_name='catalogassignment',
            name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='catalog_assignments', to='core.category'),
        ),
        migrations.AddField(
            model_name='boxselection',
            name='configuration',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='selections', to='core.boxconfiguration'),
        ),
        migrations.AddField(
            model_name='boxitem',
            name='description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='internal_notes',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='order',
            name='tracking_number',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.RunPython(migrate_data, restore_data),
        migrations.RemoveConstraint(
            model_name='boxselection',
            name='box_selection_has_one_parent',
        ),
        migrations.RemoveConstraint(
            model_name='giftboxitem',
            name='unique_gift_box_item',
        ),
        migrations.RemoveField(model_name='cartitem', name='gift_box'),
        migrations.RemoveField(model_name='cartitem', name='user'),
        migrations.RemoveField(model_name='boxselection', name='cart_item'),
        migrations.RemoveField(model_name='boxselection', name='order_item'),
        migrations.RemoveField(model_name='giftboxitem', name='box_item'),
        migrations.RemoveField(model_name='giftboxitem', name='gift_box'),
        migrations.RemoveField(model_name='orderitem', name='gift_box'),
        migrations.RemoveField(model_name='orderitem', name='order'),
        migrations.RemoveField(model_name='boxitem', name='category_name'),
        migrations.AlterModelOptions(name='boxitem', options={'ordering': ['name']}),
        migrations.AlterField(
            model_name='boxitem',
            name='gift_boxes',
            field=models.ManyToManyField(blank=True, related_name='available_items', through='core.CatalogAssignment', to='core.giftbox'),
        ),
        migrations.DeleteModel(name='CartItem'),
        migrations.DeleteModel(name='GiftBoxItem'),
        migrations.DeleteModel(name='OrderItem'),
        migrations.AddConstraint(
            model_name='boxselection',
            constraint=models.CheckConstraint(condition=models.Q(('configuration__isnull', False)), name='box_selection_configuration_required'),
        ),
        migrations.AddConstraint(
            model_name='boxselection',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='box_selection_quantity_positive'),
        ),
        migrations.AddConstraint(
            model_name='boxconfiguration',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('gift_box__isnull', True), ('order__isnull', False), ('status', 'ordered')), models.Q(('gift_box__isnull', False), ('order__isnull', False), ('status', 'ordered')), models.Q(('gift_box__isnull', False), ('order__isnull', True), ('status__in', ['draft', 'cart'])), _connector='OR'), name='configuration_state_consistent'),
        ),
        migrations.AddConstraint(
            model_name='catalogassignment',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('category__isnull', False), ('gift_box__isnull', True), ('is_default', False)), models.Q(('category__isnull', True), ('gift_box__isnull', False)), models.Q(('category__isnull', True), ('gift_box__isnull', True), ('image__isnull', False), ('is_default', False)), _connector='OR'), name='catalog_assignment_has_one_target'),
        ),
        migrations.AddConstraint(
            model_name='catalogassignment',
            constraint=models.UniqueConstraint(condition=models.Q(('category__isnull', False)), fields=('box_item', 'category'), name='unique_box_item_category'),
        ),
        migrations.AddConstraint(
            model_name='catalogassignment',
            constraint=models.UniqueConstraint(condition=models.Q(('gift_box__isnull', False)), fields=('box_item', 'gift_box'), name='unique_box_item_gift_box'),
        ),
    ]
