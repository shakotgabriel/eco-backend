"""
Seed demo accounts and agent profiles for portal login.

Does NOT seed bookings, payments, tickets, parcels, agent sales, or payouts.
Use --purge-transactions to clear all existing commerce/transaction data
while keeping users (and optional agent profiles / commission tiers).

Requires `seed_routes` (and optionally `seed_fleet`) for cities if creating agents.

Usage:
  python manage.py seed_demo_data
  python manage.py seed_demo_data --purge-transactions
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from agents.models import Agent, AgentPayout, AgentSale, CommissionTier
from bookings.models import Booking, BookingGroup, Reschedule
from cities.models import City
from parcels.models import Parcel
from payments.models import Payment, Refund
from reports.audit import AuditLog
from reports.models import Report
from seats.models import SeatLock
from tickets.models import Ticket
from trips.models import Trip

DEMO_PASSWORD = "Demo@12345"

# Rename legacy personal usernames → city-based accounts (idempotent).
LEGACY_RENAMES = {
    "staff.john": ("staff.juba", "staff.juba@ecobus.co.ss", "John", "Achiek", "+211920000003"),
    "staff.peter": ("staff.nairobi", "staff.nairobi@ecobus.co.ss", "Samuel", "Kiptoo", "+254712000003"),
    "staff.mary": ("staff.kampala", "staff.kampala@ecobus.co.ss", "Mary", "Nakato", "+256772000003"),
    "staff.grace": ("staff.kigali", "staff.kigali@ecobus.co.ss", "Claire", "Mutesi", "+250788000002"),
    "driver.john": ("staff.juba", "staff.juba@ecobus.co.ss", "John", "Achiek", "+211920000003"),
    "driver.peter": ("staff.nairobi", "staff.nairobi@ecobus.co.ss", "Samuel", "Kiptoo", "+254712000003"),
    "driver.mary": ("staff.kampala", "staff.kampala@ecobus.co.ss", "Mary", "Nakato", "+256772000003"),
    "driver.grace": ("staff.kigali", "staff.kigali@ecobus.co.ss", "Claire", "Mutesi", "+250788000002"),
}

# (username, email, first, last, role, phone, is_superuser, is_staff)
# City-scoped names only: manager.<city>, staff.<city>, agent.<city>
USERS = [
    ("admin", "admin@ecobus.co.ss", "Amina", "Deng", "ADMIN", "+211920000001", True, True),
    ("manager.nairobi", "manager.nairobi@ecobus.co.ss", "Grace", "Wanjiru", "MANAGER", "+254712000001", False, True),
    ("manager.kampala", "manager.kampala@ecobus.co.ss", "Moses", "Okello", "MANAGER", "+256772000001", False, True),
    ("manager.juba", "manager.juba@ecobus.co.ss", "James", "Garang", "MANAGER", "+211920000010", False, True),
    ("manager.kigali", "manager.kigali@ecobus.co.ss", "Alice", "Uwase", "MANAGER", "+250788000001", False, True),
    ("agent.nairobi", "agent.nairobi@ecobus.co.ss", "Brian", "Otieno", "STAFF", "+254712000002", False, False),
    ("agent.kampala", "agent.kampala@ecobus.co.ss", "Fiona", "Nabatanzi", "STAFF", "+256772000002", False, False),
    ("agent.juba", "agent.juba@ecobus.co.ss", "Peter", "Lado", "STAFF", "+211920000002", False, False),
    ("agent.kigali", "agent.kigali@ecobus.co.ss", "Diane", "Uwimana", "STAFF", "+250788000010", False, False),
    ("staff.nairobi", "staff.nairobi@ecobus.co.ss", "Samuel", "Kiptoo", "STAFF", "+254712000003", False, False),
    ("staff.kampala", "staff.kampala@ecobus.co.ss", "Mary", "Nakato", "STAFF", "+256772000003", False, False),
    ("staff.juba", "staff.juba@ecobus.co.ss", "John", "Achiek", "STAFF", "+211920000003", False, False),
    ("staff.kigali", "staff.kigali@ecobus.co.ss", "Claire", "Mutesi", "STAFF", "+250788000002", False, False),
    ("driver.nairobi", "driver.nairobi@ecobus.co.ss", "Daniel", "Mwangi", "DRIVER", "+254712000004", False, False),
    ("driver.kampala", "driver.kampala@ecobus.co.ss", "Joseph", "Ssebunya", "DRIVER", "+256772000004", False, False),
    ("driver.juba", "driver.juba@ecobus.co.ss", "Michael", "Deng", "DRIVER", "+211920000004", False, False),
    ("driver.kigali", "driver.kigali@ecobus.co.ss", "Eric", "Habimana", "DRIVER", "+250788000003", False, False),
    ("operator.nairobi", "operator.nairobi@ecobus.co.ss", "Lucy", "Njeri", "OPERATOR", "+254712000005", False, False),
    ("operator.kampala", "operator.kampala@ecobus.co.ss", "Agnes", "Namutebi", "OPERATOR", "+256772000005", False, False),
    ("operator.juba", "operator.juba@ecobus.co.ss", "Rebecca", "Akol", "OPERATOR", "+211920000005", False, False),
    ("operator.kigali", "operator.kigali@ecobus.co.ss", "Chantal", "Uwera", "OPERATOR", "+250788000004", False, False),
]

COMMISSION_TIERS = [
    ("Bronze", 0, Decimal("5.00")),
    ("Silver", 15, Decimal("7.00")),
    ("Gold", 40, Decimal("10.00")),
    ("Platinum", 80, Decimal("12.00")),
]

# (username, agent_code, business_name, city_code, phone, base_commission, referred_by_username)
AGENTS = [
    ("agent.nairobi", "AGT-NBO-001", "Nairobi Central Booking Desk", "NBO", "+254712000002", Decimal("5.00"), None),
    ("agent.kampala", "AGT-KLA-001", "Kampala Old Taxi Park Agent", "KLA", "+256772000002", Decimal("5.00"), "agent.nairobi"),
    ("agent.juba", "AGT-JUB-001", "Juba Konyo Konyo Agent", "JUB", "+211920000002", Decimal("5.00"), None),
    ("agent.kigali", "AGT-KGL-001", "Kigali Nyabugogo Agent", "KGL", "+250788000010", Decimal("5.00"), "agent.nairobi"),
]

# Drivers used as trip assignees (DRIVER role, city-scoped)
TRIP_DRIVER_USERNAMES = (
    "driver.nairobi",
    "driver.kampala",
    "driver.juba",
    "driver.kigali",
)

BUS_OPERATOR_USERNAMES = (
    "operator.nairobi",
    "operator.kampala",
    "operator.juba",
    "operator.kigali",
)


class Command(BaseCommand):
    help = "Seed demo users/agents only — no transaction or commerce data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--purge-transactions",
            action="store_true",
            help="Delete bookings, payments, tickets, parcels, agent sales, and payouts",
        )
        parser.add_argument(
            "--skip-agents",
            action="store_true",
            help="Only seed user accounts (no agent profiles or commission tiers)",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            if options["purge_transactions"]:
                self._purge_transactions()

            self._rename_legacy_users()
            users = self._seed_users()
            self._seed_exchange_rates()
            if not options["skip_agents"]:
                self._seed_commission_tiers()
                self._seed_agents(users)
                self._assign_drivers(users)
                self._assign_operators(users)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! {User.objects.count()} users, {Agent.objects.count()} agents, "
            f"{CommissionTier.objects.count()} tiers. "
            f"Commerce: bookings={Booking.objects.count()}, payments={Payment.objects.count()}, "
            f"parcels={Parcel.objects.count()}, agent_sales={AgentSale.objects.count()}."
        ))
        self.stdout.write(self.style.WARNING(
            f"\nDemo login password for all seeded accounts: {DEMO_PASSWORD}"
        ))
        self.stdout.write("Sign in with username or email:")
        for username, email, *_rest in USERS:
            self.stdout.write(f"  - {username}  /  {email}")

    def _rename_legacy_users(self):
        """Move personal demo usernames to city-scoped names when safe."""
        for old_username, (new_username, new_email, first, last, phone) in LEGACY_RENAMES.items():
            try:
                user = User.objects.get(username=old_username)
            except User.DoesNotExist:
                continue

            if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                # Target already exists — drop the legacy duplicate after moving trip FKs.
                Trip.objects.filter(driver=user).update(driver=User.objects.get(username=new_username))
                user.delete()
                self.stdout.write(f"  Removed legacy duplicate {old_username} (kept {new_username})")
                continue

            user.username = new_username
            user.email = new_email
            user.first_name = first
            user.last_name = last
            user.phone_number = phone
            user.role = "STAFF" if new_username.startswith(("staff.", "agent.")) else user.role
            if new_username.startswith("manager."):
                user.role = "MANAGER"
                user.is_staff = True
            user.save()
            self.stdout.write(f"  Renamed {old_username} → {new_username}")

    def _purge_transactions(self):
        self.stdout.write("Purging transaction / commerce data...")
        # Order matters for FKs that do not cascade automatically
        sales = AgentSale.objects.all().delete()
        payouts = AgentPayout.objects.all().delete()
        refunds = Refund.objects.all().delete()
        parcels = Parcel.objects.all().delete()
        tickets = Ticket.objects.all().delete()
        payments = Payment.objects.all().delete()
        reschedules = Reschedule.objects.all().delete()
        bookings = Booking.objects.all().delete()
        groups = BookingGroup.objects.all().delete()
        locks = SeatLock.objects.all().delete()
        reports = Report.objects.all().delete()
        audits = AuditLog.objects.filter(
            action__in=[
                "BOOKING_CREATED", "BOOKING_CANCELLED", "BOOKING_RESCHEDULED",
                "PAYMENT_COMPLETED", "PAYMENT_FAILED", "PAYMENT_REFUNDED",
                "TICKET_USED", "AGENT_COMMISSION_PAID",
            ]
        ).delete()
        self.stdout.write(
            f"  Removed sales={sales[0]}, payouts={payouts[0]}, refunds={refunds[0]}, "
            f"parcels={parcels[0]}, tickets={tickets[0]}, payments={payments[0]}, "
            f"reschedules={reschedules[0]}, bookings={bookings[0]}, groups={groups[0]}, "
            f"seat_locks={locks[0]}, reports={reports[0]}, audits={audits[0]}"
        )

    def _seed_exchange_rates(self):
        """Demo FX table — units of currency per 1 USD. Update in admin for production."""
        from payments.models import ExchangeRate

        defaults = {
            "USD": Decimal("1"),
            "KES": Decimal("129.50"),
            "UGX": Decimal("3700.00"),
            "SSP": Decimal("1500.00"),
            "RWF": Decimal("1320.00"),
        }
        for code, rate in defaults.items():
            obj, created = ExchangeRate.objects.update_or_create(
                currency=code,
                defaults={"rate_to_usd": rate},
            )
            status = "created" if created else "updated"
            self.stdout.write(f"  FX 1 USD = {rate} {code} — {status}")

    def _seed_users(self):
        users = {}
        for username, email, first, last, role, phone, is_super, is_staff_flag in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": first,
                    "last_name": last,
                    "role": role,
                    "phone_number": phone,
                    "is_superuser": is_super,
                    "is_staff": is_staff_flag or is_super,
                },
            )
            if not created:
                # Keep city-scoped accounts aligned with seed definition
                user.email = email
                user.first_name = first
                user.last_name = last
                user.role = role
                user.phone_number = phone
                user.is_superuser = is_super
                user.is_staff = is_staff_flag or is_super
                user.save()
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
            else:
                # Ensure demo password stays usable for local/staging
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
            users[username] = user
            status = "created" if created else "updated"
            self.stdout.write(f"  User {username} / {email} ({role}) — {status}")
        return users

    def _seed_commission_tiers(self):
        for name, min_sales, pct in COMMISSION_TIERS:
            tier, created = CommissionTier.objects.get_or_create(
                name=name,
                defaults={"min_monthly_sales": min_sales, "commission_pct": pct},
            )
            status = "created" if created else "exists"
            self.stdout.write(f"  Commission tier {name} ({min_sales}+ sales → {pct}%) — {status}")

    def _seed_agents(self, users):
        city_map = {c.code: c for c in City.objects.all()}
        agents = {}
        for username, code, business_name, city_code, phone, base_pct, referred_by_username in AGENTS:
            user = users[username]
            city = city_map.get(city_code)
            if city is None:
                self.stdout.write(self.style.WARNING(
                    f"  Skip agent {code}: city {city_code} missing (run seed_routes first)"
                ))
                continue
            agent, created = Agent.objects.get_or_create(
                user=user,
                defaults={
                    "agent_code": code,
                    "business_name": business_name,
                    "city": city,
                    "phone": phone,
                    "commission_pct": base_pct,
                },
            )
            if not created:
                agent.agent_code = code
                agent.business_name = business_name
                agent.city = city
                agent.phone = phone
                agent.commission_pct = base_pct
                agent.save()
            agents[username] = agent
            status = "created" if created else "updated"
            self.stdout.write(f"  Agent {code} — {business_name} — {status}")

        for username, _code, _biz, _city, _phone, _pct, referred_by_username in AGENTS:
            if not referred_by_username:
                continue
            agent = agents.get(username)
            referrer = agents.get(referred_by_username)
            if agent and referrer and agent.referred_by_id != referrer.id:
                agent.referred_by = referrer
                agent.save(update_fields=["referred_by", "updated_at"])

        return agents

    def _assign_drivers(self, users):
        drivers = [users[key] for key in TRIP_DRIVER_USERNAMES if key in users]
        if not drivers:
            return
        upcoming = list(
            Trip.objects.filter(status__in=["SCHEDULED", "BOARDING"], driver__isnull=True)
            .order_by("departure_time")[:40]
        )
        for i, trip in enumerate(upcoming):
            trip.driver = drivers[i % len(drivers)]
            trip.save(update_fields=["driver", "updated_at"])
        if upcoming:
            self.stdout.write(f"  Assigned trip drivers to {len(upcoming)} upcoming trips")

    def _assign_operators(self, users):
        from buses.models import Bus

        operators = [users[key] for key in BUS_OPERATOR_USERNAMES if key in users]
        if not operators:
            return
        buses = list(Bus.objects.filter(operator__isnull=True).order_by("id")[:40])
        for i, bus in enumerate(buses):
            bus.operator = operators[i % len(operators)]
            bus.save(update_fields=["operator", "updated_at"])
        if buses:
            self.stdout.write(f"  Assigned operators to {len(buses)} buses")
