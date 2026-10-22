from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_boxconfiguration_catalogassignment_category_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='catalogassignment',
            name='catalog_assignment_has_one_target',
        ),
        migrations.AddConstraint(
            model_name='catalogassignment',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(category__isnull=False, gift_box__isnull=True, is_default=False)
                    | models.Q(category__isnull=True, gift_box__isnull=False)
                    | models.Q(
                        category__isnull=True,
                        gift_box__isnull=True,
                        image__isnull=False,
                        is_default=False,
                    )
                ),
                name='catalog_assignment_has_one_target',
            ),
        ),
    ]
