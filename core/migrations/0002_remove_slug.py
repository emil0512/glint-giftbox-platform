from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='eventoccasion',
            name='slug',
        ),
        migrations.RemoveField(
            model_name='giftbox',
            name='slug',
        ),
    ]
