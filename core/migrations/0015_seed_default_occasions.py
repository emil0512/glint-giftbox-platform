from django.db import migrations


OCCASION_NAMES = (
    'Birthday',
    'Anniversary',
    "Valentine's Day",
    'Graduation',
    'Baby Shower',
    'Christmas & Holidays',
    "Mother's Day",
    'Wedding & Engagement',
)


def seed_default_occasions(apps, schema_editor):
    EventOccasion = apps.get_model('core', 'EventOccasion')
    database = schema_editor.connection.alias
    for name in OCCASION_NAMES:
        EventOccasion.objects.using(database).get_or_create(
            name=name,
            defaults={'is_active': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_preserve_order_time_facts'),
    ]

    operations = [
        migrations.RunPython(seed_default_occasions, migrations.RunPython.noop),
    ]
