import time

from django.core.management.base import BaseCommand

from consultations.billing import bill_active_sessions


class Command(BaseCommand):
    help = "Bill all in-progress sessions for elapsed full minutes. Run from cron, or with --loop."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            type=int,
            metavar="SECONDS",
            help="Keep running, billing every SECONDS seconds (simple stand-in for a background worker).",
        )

    def handle(self, *args, loop=None, **options):
        while True:
            for session in bill_active_sessions():
                self.stdout.write(
                    f"session {session.pk}: status={session.status} "
                    f"billed_minutes={session.billed_minutes} billed_amount={session.billed_amount}"
                )
            if not loop:
                return
            time.sleep(loop)
