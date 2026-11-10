from django.db import models


class ContactMessage(models.Model):
    """Store customer contact form submissions."""
    
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=[
            ("NEW", "New"),
            ("ACKNOWLEDGED", "Acknowledged"),
            ("RESOLVED", "Resolved"),
        ],
        default="NEW",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return f"{self.subject} - {self.name} ({self.created_at.date()})"
