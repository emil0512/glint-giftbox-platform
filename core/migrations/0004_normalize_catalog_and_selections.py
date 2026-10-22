from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def normalize_existing_data(apps, schema_editor):
    ItemCategory = apps.get_model('core', 'ItemCategory')
    BoxItem = apps.get_model('core', 'BoxItem')
    CartItem = apps.get_model('core', 'CartItem')
    CartItemSelection = apps.get_model('core', 'CartItemSelection')
    OrderItem = apps.get_model('core', 'OrderItem')
    OrderItemSelection = apps.get_model('core', 'OrderItemSelection')
    db_alias = schema_editor.connection.alias

    for item in BoxItem.objects.using(db_alias).all().iterator():
        category, _ = ItemCategory.objects.using(db_alias).get_or_create(
            name=item.category_name or 'General'
        )
        item.category_id = category.pk
        item.save(update_fields=['category'], using=db_alias)

    for cart_item in CartItem.objects.using(db_alias).all().iterator():
        for selected in cart_item.selected_items_data:
            box_item = BoxItem.objects.using(db_alias).filter(pk=selected.get('id')).first()
            CartItemSelection.objects.using(db_alias).create(
                cart_item_id=cart_item.pk,
                box_item_id=box_item.pk if box_item else None,
                item_name=selected.get('name') or (box_item.name if box_item else 'Unavailable item'),
                unit_price=Decimal(str(selected.get('price', box_item.price if box_item else 0))),
                quantity=max(1, int(selected.get('qty', 1))),
            )

    for order_item in OrderItem.objects.using(db_alias).all().iterator():
        for selected in order_item.selected_items_data:
            box_item = BoxItem.objects.using(db_alias).filter(pk=selected.get('id')).first()
            OrderItemSelection.objects.using(db_alias).create(
                order_item_id=order_item.pk,
                box_item_id=box_item.pk if box_item else None,
                item_name=selected.get('name') or (box_item.name if box_item else 'Unavailable item'),
                unit_price=Decimal(str(selected.get('price', box_item.price if box_item else 0))),
                quantity=max(1, int(selected.get('qty', 1))),
            )


def restore_legacy_data(apps, schema_editor):
    BoxItem = apps.get_model('core', 'BoxItem')
    CartItem = apps.get_model('core', 'CartItem')
    OrderItem = apps.get_model('core', 'OrderItem')
    db_alias = schema_editor.connection.alias

    for item in BoxItem.objects.using(db_alias).all().iterator():
        item.category_name = item.category.name
        item.save(update_fields=['category_name'], using=db_alias)

    for cart_item in CartItem.objects.using(db_alias).all().iterator():
        cart_item.selected_items_data = [
            {
                'id': selection.box_item_id,
                'name': selection.item_name,
                'category': selection.box_item.category.name if selection.box_item_id else 'General',
                'price': float(selection.unit_price),
                'qty': selection.quantity,
            }
            for selection in cart_item.selections.using(db_alias).all()
        ]
        cart_item.save(update_fields=['selected_items_data'], using=db_alias)

    for order_item in OrderItem.objects.using(db_alias).all().iterator():
        order_item.selected_items_data = [
            {
                'id': selection.box_item_id,
                'name': selection.item_name,
                'category': selection.box_item.category.name if selection.box_item_id else 'General',
                'price': float(selection.unit_price),
                'qty': selection.quantity,
            }
            for selection in order_item.selections.using(db_alias).all()
        ]
        order_item.save(update_fields=['selected_items_data'], using=db_alias)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0003_remove_userprofile_failed_login_attempts_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ItemCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='boxitem',
            name='category',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='items', to='core.itemcategory'),
        ),
        migrations.CreateModel(
            name='CartItemSelection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_name', models.CharField(max_length=150)),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('box_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.boxitem')),
                ('cart_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='selections', to='core.cartitem')),
            ],
        ),
        migrations.CreateModel(
            name='OrderItemSelection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_name', models.CharField(max_length=150)),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('box_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.boxitem')),
                ('order_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='selections', to='core.orderitem')),
            ],
        ),
        migrations.RunPython(normalize_existing_data, restore_legacy_data),
        migrations.RemoveField(model_name='boxitem', name='category_name'),
        migrations.RemoveField(model_name='cartitem', name='selected_items_data'),
        migrations.RemoveField(model_name='orderitem', name='selected_items_data'),
        migrations.AlterField(
            model_name='boxitem',
            name='category',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='items', to='core.itemcategory'),
        ),
        migrations.AlterModelOptions(
            name='boxitem',
            options={'ordering': ['category__name', 'name']},
        ),
    ]
