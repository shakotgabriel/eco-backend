"""Celery tasks for parcel notifications."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    "PENDING": (
        "sender",
        "Your parcel {tracking} has been registered with EcoBus. "
        "Route: {route}. Tracking: {tracking}. Receiver: {receiver}.",
    ),
    "IN_TRANSIT": (
        "both",
        "Parcel {tracking} is now in transit from {origin} to {destination}. "
        "Expected arrival: {arrival}.",
    ),
    "ARRIVED": (
        "receiver",
        "Parcel {tracking} has arrived at {destination}! "
        "Your pickup code is: {pickup_code}. "
        "Please present this code to collect your parcel.",
    ),
    "DELIVERED": (
        "both",
        "Parcel {tracking} has been delivered successfully. Thank you for using EcoBus!",
    ),
    "CANCELLED": (
        "both",
        "Parcel {tracking} has been cancelled. Contact EcoBus for details.",
    ),
}


@shared_task(bind=True, max_retries=2)
def notify_parcel_status_change(self, parcel_id, new_status):
    """Send SMS to sender and/or receiver on parcel status change."""
    from bebeto_backend.services.twilio_sms import notify_customer
    from .models import Parcel

    try:
        parcel = Parcel.objects.select_related(
            "trip__route__origin", "trip__route__destination"
        ).get(id=parcel_id)
    except Parcel.DoesNotExist:
        logger.warning(f"Parcel {parcel_id} not found for notification")
        return

    template = STATUS_MESSAGES.get(new_status)
    if not template:
        return

    audience, msg_template = template
    route = parcel.trip.route

    context = {
        "tracking": parcel.tracking_number,
        "route": f"{route.origin.name} → {route.destination.name}",
        "origin": route.origin.name,
        "destination": route.destination.name,
        "arrival": parcel.trip.arrival_time.strftime("%d %b %Y, %H:%M") if parcel.trip.arrival_time else "TBD",
        "receiver": parcel.receiver_name,
        "sender": parcel.sender_name,
        "pickup_code": parcel.pickup_code or "",
    }
    message = msg_template.format(**context)

    recipients = []
    if audience in ("sender", "both"):
        recipients.append(("sender", str(parcel.sender_phone)))
    if audience in ("receiver", "both"):
        recipients.append(("receiver", str(parcel.receiver_phone)))

    for role, phone in recipients:
        try:
            notify_customer(phone, message)
            logger.info(f"Parcel SMS sent to {role} ({phone}) for {parcel.tracking_number}")
        except Exception as exc:
            logger.error(f"Parcel SMS failed to {role} ({phone}): {exc}")
