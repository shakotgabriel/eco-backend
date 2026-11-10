from decimal import Decimal

from rest_framework import serializers

from tickets.serializers import TicketCreateSerializer
from .models import Booking, BookingGroup, Reschedule


class BookingGroupSerializer(serializers.ModelSerializer):
    bookings = serializers.SerializerMethodField()

    class Meta:
        model = BookingGroup
        fields = ["id", "group_reference", "trip_type", "discount_pct", "bookings", "created_at"]
        read_only_fields = ["id", "group_reference", "created_at"]

    def get_bookings(self, obj):
        return BookingSerializer(obj.bookings.order_by("leg_order"), many=True).data


class BookingSerializer(serializers.ModelSerializer):
    trip_display = serializers.CharField(source="trip.__str__", read_only=True)
    group_reference = serializers.CharField(source="group.group_reference", read_only=True, default=None)

    class Meta:
        model = Booking
        fields = [
            "id", "booking_reference", "group", "group_reference", "leg_order",
            "user", "trip", "trip_display",
            "payer_name", "payment_phone", "email",
            "number_of_seats", "total_price", "status", "expires_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "booking_reference", "user", "created_at", "updated_at"]


class BookingCreateSerializer(serializers.ModelSerializer):
    """Used by the public booking flow — accepts nested ticket/passenger data."""
    tickets = TicketCreateSerializer(many=True)
    payment_method = serializers.ChoiceField(
        choices=["MOBILE_MONEY", "MTN_MOMO", "MPESA", "CASH"],
        default="MOBILE_MONEY",
        write_only=True,
        help_text="MOBILE_MONEY triggers AT STK push; MTN_MOMO sends MoMo USSD prompt; MPESA triggers Safaricom STK push; CASH means pay at counter.",
    )

    class Meta:
        model = Booking
        fields = [
            "trip", "payer_name", "payment_phone", "email",
            "tickets", "payment_method",
        ]

    def validate_tickets(self, value):
        if not value:
            raise serializers.ValidationError("At least one ticket (passenger) is required.")
        return value

    def validate(self, attrs):
        from seats.models import Seat, SeatLock
        from django.utils import timezone

        trip = attrs["trip"]
        ticket_count = len(attrs["tickets"])

        if trip.status not in ("SCHEDULED", "BOARDING"):
            raise serializers.ValidationError(
                {"trip": "This trip is not available for booking."}
            )
        if ticket_count > trip.available_seats:
            raise serializers.ValidationError(
                {"tickets": f"Only {trip.available_seats} seat(s) available, but {ticket_count} requested."}
            )

        # Collect all chosen seat IDs and check for duplicates
        chosen_seat_ids = []
        for i, ticket_data in enumerate(attrs["tickets"]):
            seat_id = ticket_data.get("seat")
            if seat_id in chosen_seat_ids:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i + 1}: seat {seat_id} is selected more than once."}
                )
            chosen_seat_ids.append(seat_id)

        # Bulk-fetch the seats
        bus = trip.bus
        seats_by_id = {
            s.id: s for s in Seat.objects.filter(id__in=chosen_seat_ids, bus=bus, is_active=True)
        }

        # Already-booked seat IDs on this trip
        booked_seat_ids = set(
            trip.bookings.exclude(status="CANCELLED")
            .values_list("tickets__seat_id", flat=True)
        ) - {None}

        # Currently-locked seat IDs (by other sessions)
        now = timezone.now()
        session_key = ""
        request = self.context.get("request")
        if request and hasattr(request, "data"):
            session_key = request.data.get("session_key", "") or ""
        # Staff/agent desk locks seats as user-{id}; treat that as own hold
        # even if session_key was omitted from the booking payload.
        if not session_key and request and getattr(request, "user", None) and request.user.is_authenticated:
            session_key = f"user-{request.user.id}"
        locked_seat_ids = set(
            SeatLock.objects.filter(trip=trip, expires_at__gt=now)
            .exclude(session_key=session_key)
            .values_list("seat_id", flat=True)
        ) if session_key else set(
            SeatLock.objects.filter(trip=trip, expires_at__gt=now)
            .values_list("seat_id", flat=True)
        )

        for i, ticket_data in enumerate(attrs["tickets"]):
            seat_id = ticket_data["seat"]
            seat_class = ticket_data.get("seat_class", "REGULAR")

            # 1. Seat must belong to this bus
            seat_obj = seats_by_id.get(seat_id)
            if not seat_obj:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i + 1}: seat {seat_id} does not belong to this trip's bus or is inactive."}
                )

            # 2. Seat class must match the physical seat — coerce to the seat's type
            # so clients can't mislabel VIP as REGULAR (or vice versa).
            if seat_obj.seat_type != seat_class:
                ticket_data["seat_class"] = seat_obj.seat_type
                seat_class = seat_obj.seat_type

            # 3. Seat must not be already booked
            if seat_id in booked_seat_ids:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i + 1}: seat {seat_id} is already booked on this trip."}
                )

            # 4. Seat must not be locked by another user
            if seat_id in locked_seat_ids:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i + 1}: seat {seat_id} is temporarily held by another customer."}
                )

            # 5. Validate seat_class price exists on the trip
            price_field = f"{seat_class.lower()}_price"
            trip_price = getattr(trip, price_field, None)
            if trip_price is None:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i + 1}: {seat_class} class is not available on this trip."}
                )

        return attrs

    def create(self, validated_data):
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone
        from payments.models import Payment
        from payments.tasks import initiate_mobile_money_payment, initiate_momo_payment
        from payments.tasks import poll_mpesa_payment_status
        from seats.models import SeatLock
        from tickets.models import Ticket

        tickets_data = validated_data.pop("tickets")
        payment_method = validated_data.pop("payment_method", "MOBILE_MONEY")
        trip = validated_data["trip"]

        # Calculate total price
        total_price = 0
        for ticket_data in tickets_data:
            seat_class = ticket_data.get("seat_class", "REGULAR")
            price_field = f"{seat_class.lower()}_price"
            ticket_data["price"] = getattr(trip, price_field)
            total_price += ticket_data["price"]

        # Get configurable booking expiry timeout
        expiry_config = getattr(settings, "BOOKING_EXPIRY_CONFIG", {
            "default_minutes": 5,
            "by_method": {},
        })
        expiry_minutes = expiry_config.get("by_method", {}).get(
            payment_method,
            expiry_config.get("default_minutes", 5)
        )

        now = timezone.now()

        # Ensure atomicity and prevent races on trip/seat availability
        from django.db import transaction
        from django.db.models import F
        from trips.models import Trip
        from seats.models import Seat

        with transaction.atomic():
            # Lock the trip row to protect available_seats
            trip_locked = Trip.objects.select_for_update().get(pk=trip.pk)

            ticket_count = len(tickets_data)
            if ticket_count > trip_locked.available_seats:
                raise serializers.ValidationError({"tickets": f"Only {trip_locked.available_seats} seat(s) available, but {ticket_count} requested."})

            # Lock selected seat rows to avoid double-booking the same seat
            chosen_seat_ids = [t.get("seat") for t in tickets_data]
            seats = list(Seat.objects.select_for_update().filter(id__in=chosen_seat_ids, bus=trip_locked.bus, is_active=True))
            seats_by_id = {s.id: s for s in seats}

            # Re-run seat validations (conservative check)
            booked_seat_ids = set(
                trip_locked.bookings.exclude(status="CANCELLED")
                .values_list("tickets__seat_id", flat=True)
            ) - {None}

            for i, ticket_data in enumerate(tickets_data):
                sid = ticket_data["seat"]
                seat_obj = seats_by_id.get(sid)
                if not seat_obj:
                    raise serializers.ValidationError({"tickets": f"Ticket {i + 1}: seat {sid} does not belong to this trip's bus or is inactive."})
                if sid in booked_seat_ids:
                    raise serializers.ValidationError({"tickets": f"Ticket {i + 1}: seat {sid} is already booked on this trip."})

            # Create booking — attribute authenticated desk staff/agents as seller
            request = self.context.get("request")
            seller = None
            if request and getattr(request, "user", None) and request.user.is_authenticated:
                seller = request.user

            booking = Booking.objects.create(
                **validated_data,
                user=seller,
                number_of_seats=ticket_count,
                total_price=total_price,
                expires_at=now + timedelta(minutes=expiry_minutes),
            )

            for ticket_data in tickets_data:
                ticket_data["seat_id"] = ticket_data.pop("seat")
                Ticket.objects.create(booking=booking, **ticket_data)

            # Release any seat locks held by this session (seats are now booked)
            request = self.context.get("request")
            sk = ""
            if request and hasattr(request, "data"):
                sk = request.data.get("session_key", "") or ""
            if not sk and request and getattr(request, "user", None) and request.user.is_authenticated:
                sk = f"user-{request.user.id}"
            if sk:
                SeatLock.objects.filter(trip=trip_locked, session_key=sk).delete()

            # Decrement available seats atomically
            Trip.objects.filter(pk=trip_locked.pk).update(available_seats=F("available_seats") - ticket_count)

            # Create a Payment record and trigger the appropriate payment flow
            route_currency = trip_locked.route.currency
            if payment_method == "MTN_MOMO":
                from payments.mtn_momo import collection_currency_for_msisdn
                currency = collection_currency_for_msisdn(validated_data.get("payment_phone"))
            elif payment_method == "MPESA":
                currency = "KES"
            elif payment_method == "CASH":
                currency = route_currency
            else:
                currency = route_currency
            payment = Payment.objects.create(
                booking=booking,
                amount=total_price,
                currency=currency,
                original_amount=total_price,
                original_currency=route_currency,
                exchange_rate=1 if currency == route_currency else None,
                method=payment_method,
                status="PENDING",
            )

        # Trigger async payment flows outside the DB lock where possible
        if payment_method == "MOBILE_MONEY":
            initiate_mobile_money_payment.delay(payment.pk)
        elif payment_method == "MTN_MOMO":
            initiate_momo_payment.delay(payment.pk)
        elif payment_method == "MPESA":
            # M-Pesa STK Push initiated via the dedicated endpoint
            # (POST /api/v1/payments/mpesa/stk-push/) after booking is created
            pass

        # Optional metrics: increment booking created counter if available
        try:
            from payments.metrics import increment_counter
            increment_counter("bookings_created")
        except Exception:
            pass

        return booking


class LegSerializer(serializers.Serializer):
    """One leg of a multi-leg or return booking."""
    trip = serializers.IntegerField()
    tickets = TicketCreateSerializer(many=True)


class MultiLegBookingSerializer(serializers.Serializer):
    """
    Book a round-trip or multi-leg journey in one request.
    Creates a BookingGroup + one Booking per leg.
    """
    trip_type = serializers.ChoiceField(choices=["RETURN", "MULTI_LEG"])
    payer_name = serializers.CharField(max_length=200)
    payment_phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, default="")
    payment_method = serializers.ChoiceField(choices=["MOBILE_MONEY", "MTN_MOMO", "MPESA", "CASH"], default="MOBILE_MONEY")
    return_discount_pct = serializers.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5.00"))
    legs = LegSerializer(many=True)

    def validate_legs(self, value):
        if len(value) < 2:
            raise serializers.ValidationError("At least 2 legs are required for multi-leg booking.")
        return value

    def validate(self, attrs):
        from django.utils import timezone
        from seats.models import Seat, SeatLock
        from trips.models import Trip

        now = timezone.now()

        for i, leg in enumerate(attrs["legs"]):
            try:
                trip = Trip.objects.select_related("bus").get(pk=leg["trip"])
            except Trip.DoesNotExist:
                raise serializers.ValidationError({"legs": f"Leg {i+1}: trip {leg['trip']} does not exist."})
            if trip.status not in ("SCHEDULED", "BOARDING"):
                raise serializers.ValidationError({"legs": f"Leg {i+1}: trip is not available."})
            ticket_count = len(leg["tickets"])
            if ticket_count > trip.available_seats:
                raise serializers.ValidationError(
                    {"legs": f"Leg {i+1}: only {trip.available_seats} seats available, {ticket_count} requested."}
                )

            # Validate seats for this leg
            chosen_seat_ids = []
            for j, td in enumerate(leg["tickets"]):
                seat_id = td.get("seat")
                if seat_id in chosen_seat_ids:
                    raise serializers.ValidationError(
                        {"legs": f"Leg {i+1}, ticket {j+1}: seat {seat_id} is selected more than once."}
                    )
                chosen_seat_ids.append(seat_id)

            seats_by_id = {
                s.id: s for s in Seat.objects.filter(id__in=chosen_seat_ids, bus=trip.bus, is_active=True)
            }
            booked_seat_ids = set(
                trip.bookings.exclude(status="CANCELLED")
                .values_list("tickets__seat_id", flat=True)
            ) - {None}
            locked_seat_ids = set(
                SeatLock.objects.filter(trip=trip, expires_at__gt=now)
                .values_list("seat_id", flat=True)
            )

            for j, td in enumerate(leg["tickets"]):
                seat_id = td["seat"]
                seat_class = td.get("seat_class", "REGULAR")
                seat_obj = seats_by_id.get(seat_id)
                if not seat_obj:
                    raise serializers.ValidationError(
                        {"legs": f"Leg {i+1}, ticket {j+1}: seat {seat_id} does not belong to this bus."}
                    )
                if seat_obj.seat_type != seat_class:
                    raise serializers.ValidationError(
                        {"legs": f"Leg {i+1}, ticket {j+1}: seat {seat_id} is {seat_obj.seat_type}, not {seat_class}."}
                    )
                if seat_id in booked_seat_ids:
                    raise serializers.ValidationError(
                        {"legs": f"Leg {i+1}, ticket {j+1}: seat {seat_id} is already booked."}
                    )
                if seat_id in locked_seat_ids:
                    raise serializers.ValidationError(
                        {"legs": f"Leg {i+1}, ticket {j+1}: seat {seat_id} is temporarily held by another customer."}
                    )
                price_field = f"{seat_class.lower()}_price"
                if getattr(trip, price_field, None) is None:
                    raise serializers.ValidationError(
                        {"legs": f"Leg {i+1}, ticket {j+1}: {seat_class} class not available on this trip."}
                    )

            leg["_trip_obj"] = trip
        return attrs

    def create(self, validated_data):
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone
        from payments.models import Payment
        from payments.tasks import initiate_mobile_money_payment, initiate_momo_payment
        from tickets.models import Ticket

        legs_data = validated_data.pop("legs")
        payment_method = validated_data.pop("payment_method", "MOBILE_MONEY")
        discount_pct = validated_data.pop("return_discount_pct", Decimal("5.00"))
        trip_type = validated_data.pop("trip_type")

        group = BookingGroup.objects.create(trip_type=trip_type, discount_pct=discount_pct)
        now = timezone.now()
        discount_factor = Decimal("1") - (discount_pct / Decimal("100"))
        bookings = []
        grand_total = Decimal("0")

        # Lock all involved trips (by id) to avoid races and deadlocks — lock in deterministic order
        from django.db import transaction
        from django.db.models import F
        from trips.models import Trip
        from seats.models import Seat

        trip_ids = [leg["_trip_obj"].pk for leg in legs_data]
        with transaction.atomic():
            trips_locked = list(Trip.objects.select_for_update().filter(pk__in=trip_ids).order_by("pk"))
            trips_by_id = {t.pk: t for t in trips_locked}

            for order, leg in enumerate(legs_data, start=1):
                trip = leg.pop("_trip_obj")
                trip_locked = trips_by_id.get(trip.pk)
                tickets_data = leg["tickets"]

                total_price = Decimal("0")
                for td in tickets_data:
                    seat_class = td.get("seat_class", "REGULAR")
                    td["price"] = getattr(trip_locked, f"{seat_class.lower()}_price")
                    total_price += td["price"]

                # Apply discount on return / later legs
                if order > 1:
                    total_price = (total_price * discount_factor).quantize(Decimal("0.01"))

                ticket_count = len(tickets_data)
                if ticket_count > trip_locked.available_seats:
                    raise serializers.ValidationError({"legs": f"Leg {order}: only {trip_locked.available_seats} seats available, {ticket_count} requested."})

                # Lock seats for this leg
                chosen_seat_ids = [td.get("seat") for td in tickets_data]
                seats = list(Seat.objects.select_for_update().filter(id__in=chosen_seat_ids, bus=trip_locked.bus, is_active=True))
                seats_by_id = {s.id: s for s in seats}

                booked_seat_ids = set(
                    trip_locked.bookings.exclude(status="CANCELLED").values_list("tickets__seat_id", flat=True)
                ) - {None}

                for j, td in enumerate(tickets_data):
                    sid = td["seat"]
                    seat_obj = seats_by_id.get(sid)
                    if not seat_obj:
                        raise serializers.ValidationError({"legs": f"Leg {order}, ticket {j+1}: seat {sid} does not belong to this bus."})
                    if sid in booked_seat_ids:
                        raise serializers.ValidationError({"legs": f"Leg {order}, ticket {j+1}: seat {sid} is already booked."})

                booking = Booking.objects.create(
                    group=group,
                    leg_order=order,
                    trip=trip_locked,
                    payer_name=validated_data["payer_name"],
                    payment_phone=validated_data["payment_phone"],
                    email=validated_data.get("email", ""),
                    number_of_seats=ticket_count,
                    total_price=total_price,
                    expires_at=now + timedelta(minutes=2),
                )

                for td in tickets_data:
                    td["seat_id"] = td.pop("seat")
                    Ticket.objects.create(booking=booking, **td)

                # Decrement available seats on this trip
                Trip.objects.filter(pk=trip_locked.pk).update(available_seats=F("available_seats") - ticket_count)

                grand_total += total_price
                bookings.append(booking)

        # Single payment for the whole group
        first_booking = bookings[0]
        if payment_method == "MTN_MOMO":
            from payments.mtn_momo import collection_currency_for_msisdn
            currency = collection_currency_for_msisdn(validated_data["payment_phone"])
        else:
            currency = "SSP"
        payment = Payment.objects.create(
            booking=first_booking,
            amount=grand_total,
            currency=currency,
            method=payment_method,
            status="PENDING",
        )

        if payment_method == "MOBILE_MONEY":
            initiate_mobile_money_payment.delay(payment.pk)
        elif payment_method == "MTN_MOMO":
            initiate_momo_payment.delay(payment.pk)

        return group


class RescheduleSerializer(serializers.ModelSerializer):
    """Read serializer for reschedule records."""
    original_booking_ref = serializers.CharField(source="original_booking.booking_reference", read_only=True)
    new_booking_ref = serializers.CharField(source="new_booking.booking_reference", read_only=True)

    class Meta:
        model = Reschedule
        fields = [
            "id", "original_booking", "original_booking_ref",
            "new_booking", "new_booking_ref",
            "original_fare", "new_fare", "fare_difference",
            "reschedule_fee", "total_due", "status", "reason",
            "rescheduled_by", "created_at",
        ]
        read_only_fields = fields


class RescheduleRequestSerializer(serializers.Serializer):
    """
    Input for the reschedule endpoint.
    POST /api/v1/bookings/{booking_id}/reschedule/
    """
    new_trip = serializers.IntegerField(help_text="ID of the new trip to rebook onto")
    tickets = TicketCreateSerializer(many=True, help_text="New seat assignments for each passenger")
    reason = serializers.CharField(required=False, default="", help_text="Reason for reschedule")
    payment_method = serializers.ChoiceField(
        choices=["MOBILE_MONEY", "MTN_MOMO", "MPESA", "CASH"],
        default="CASH",
        help_text="Payment method for any fare difference",
    )

    def validate_tickets(self, value):
        if not value:
            raise serializers.ValidationError("At least one ticket (passenger) is required.")
        return value

    def validate(self, attrs):
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone
        from seats.models import Seat, SeatLock
        from trips.models import Trip

        # Validate new trip
        try:
            new_trip = Trip.objects.select_related("bus").get(pk=attrs["new_trip"])
        except Trip.DoesNotExist:
            raise serializers.ValidationError({"new_trip": "Trip does not exist."})

        if new_trip.status not in ("SCHEDULED", "BOARDING"):
            raise serializers.ValidationError({"new_trip": "New trip is not available for booking."})

        # Check reschedule cutoff
        cutoff_hours = getattr(settings, "RESCHEDULE_CUTOFF_HOURS", 6)
        now = timezone.now()
        if new_trip.departure_time <= now + timedelta(hours=cutoff_hours):
            raise serializers.ValidationError(
                {"new_trip": f"Cannot reschedule to a trip departing within {cutoff_hours} hours."}
            )

        ticket_count = len(attrs["tickets"])
        if ticket_count > new_trip.available_seats:
            raise serializers.ValidationError(
                {"tickets": f"Only {new_trip.available_seats} seat(s) available on the new trip."}
            )

        # Validate seats on the new trip
        chosen_seat_ids = []
        for i, td in enumerate(attrs["tickets"]):
            seat_id = td.get("seat")
            if seat_id in chosen_seat_ids:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i+1}: seat {seat_id} is selected more than once."}
                )
            chosen_seat_ids.append(seat_id)

        seats_by_id = {
            s.id: s for s in Seat.objects.filter(id__in=chosen_seat_ids, bus=new_trip.bus, is_active=True)
        }
        booked_seat_ids = set(
            new_trip.bookings.exclude(status="CANCELLED")
            .values_list("tickets__seat_id", flat=True)
        ) - {None}
        locked_seat_ids = set(
            SeatLock.objects.filter(trip=new_trip, expires_at__gt=now)
            .values_list("seat_id", flat=True)
        )

        for i, td in enumerate(attrs["tickets"]):
            seat_id = td["seat"]
            seat_class = td.get("seat_class", "REGULAR")
            seat_obj = seats_by_id.get(seat_id)
            if not seat_obj:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i+1}: seat {seat_id} does not belong to the new trip's bus."}
                )
            if seat_obj.seat_type != seat_class:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i+1}: seat {seat_id} is {seat_obj.seat_type}, not {seat_class}."}
                )
            if seat_id in booked_seat_ids:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i+1}: seat {seat_id} is already booked."}
                )
            if seat_id in locked_seat_ids:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i+1}: seat {seat_id} is temporarily held by another customer."}
                )
            price_field = f"{seat_class.lower()}_price"
            if getattr(new_trip, price_field, None) is None:
                raise serializers.ValidationError(
                    {"tickets": f"Ticket {i+1}: {seat_class} class not available on the new trip."}
                )

        attrs["_new_trip_obj"] = new_trip
        return attrs
