import unittest
from app import app, db, User

class EduWayTestCase(unittest.TestCase):
    def setUp(self):
        # Set up a temporary, in-memory database for testing
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        # Disable CSRF tokens for testing purposes if necessary
        app.config['WTF_CSRF_ENABLED'] = False 
        self.app = app.test_client()
        
        with app.app_context():
            db.create_all()

    def tearDown(self):
        # Clean up the database after every test
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_a_home_page_redirects_unauthorized(self):
        """Test that a logged-out user cannot see the dashboard."""
        response = self.app.get('/')
        # 302 is the HTTP status code for a Redirect (sending them to /login)
        self.assertEqual(response.status_code, 302) 

    def test_b_user_registration(self):
        """Test that a new user can be created and saved to the database."""
        response = self.app.post('/register', data=dict(
            username='test_scholar',
            password='password123',
            full_name='Test Student',
            bio='Software Design Tester'
        ), follow_redirects=True)
        
        # Check if the page loaded successfully after registering
        self.assertEqual(response.status_code, 200)
        
        # Verify the user actually exists in the database
        with app.app_context():
            user = User.query.filter_by(username='test_scholar').first()
            self.assertIsNotNone(user)
            self.assertEqual(user.full_name, 'Test Student')
            self.assertEqual(user.points, 0) # Should start with 0 points

    def test_c_user_login(self):
        """Test that an existing user can log in."""
        # 1. Register the user
        self.app.post('/register', data=dict(
            username='test_scholar', password='password123', full_name='Test', bio='Bio'
        ))
        
        # 2. Attempt to log in
        response = self.app.post('/login', data=dict(
            username='test_scholar',
            password='password123'
        ), follow_redirects=True)
        
        # 3. Check if the dashboard text is in the response (meaning login worked)
        self.assertIn(b'Dashboard', response.data)

if __name__ == '__main__':
    unittest.main()