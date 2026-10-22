from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_alter_eventoccasion_name_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='boxconfiguration',
            old_name='box_name',
            new_name='box_name_at_order',
        ),
        migrations.RenameField(
            model_name='boxconfiguration',
            old_name='base_price',
            new_name='base_price_at_order',
        ),
        migrations.RenameField(
            model_name='boxconfiguration',
            old_name='unit_price_snapshot',
            new_name='unit_price_at_order',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='full_name',
            new_name='contact_full_name',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='email',
            new_name='contact_email',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='phone',
            new_name='contact_phone',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='shipping_address',
            new_name='ship_to_address',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='city',
            new_name='ship_to_city',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='state',
            new_name='ship_to_state',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='postal_code',
            new_name='ship_to_postal_code',
        ),
        migrations.RenameField(
            model_name='boxselection',
            old_name='item_name',
            new_name='item_name_at_order',
        ),
        migrations.AlterField(
            model_name='boxselection',
            name='item_name_at_order',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.RenameField(
            model_name='boxselection',
            old_name='unit_price',
            new_name='unit_price_at_order',
        ),
        migrations.AlterField(
            model_name='boxselection',
            name='unit_price_at_order',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
