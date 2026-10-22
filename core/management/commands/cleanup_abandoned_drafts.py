from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import BoxConfiguration


class Command(BaseCommand):
    help = 'Delete saved gift-box drafts that have been inactive for at least 30 days.'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        abandoned_drafts = BoxConfiguration.objects.filter(
            status=BoxConfiguration.DRAFT,
            updated_at__lt=cutoff,
        )
        count = abandoned_drafts.count()
        abandoned_drafts.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} abandoned draft(s).'))
