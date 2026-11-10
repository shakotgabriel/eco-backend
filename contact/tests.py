from django.test import TestCase
from .models import ContactMessage


class ContactMessageTests(TestCase):
    """Test suite for contact messages."""
    
    def test_create_contact_message(self):
        """Test creating a contact message."""
        msg = ContactMessage.objects.create(
            name="Test User",
            email="test@example.com",
            phone="256771234567",
            subject="Test Subject",
            message="Test message",
        )
        self.assertEqual(msg.status, "NEW")
        self.assertEqual(msg.name, "Test User")
        
    def test_contact_message_str(self):
        """Test contact message string representation."""
        msg = ContactMessage.objects.create(
            name="John",
            email="john@example.com",
            subject="Question",
            message="Test",
        )
        self.assertIn("Question", str(msg))
        self.assertIn("John", str(msg))
