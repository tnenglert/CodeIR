use crate::db::Database;
use crate::errors::AppError;
use crate::models::{TokenPayload, User};

/// Handles authentication operations.
pub struct AuthHandler {
    db: Database,
    secret_key: String,
}

impl AuthHandler {
    pub fn new(db: Database, secret_key: String) -> Self {
        Self { db, secret_key }
    }

    /// Authenticate a user by username and password.
    pub async fn login(
        &self,
        username: &str,
        password: &str,
    ) -> Result<String, AppError> {
        let user = self.db.find_user_by_name(username).await?;
        if !self.verify_password(password, &user) {
            return Err(AppError::Auth("Invalid credentials".into()));
        }
        let token = self.generate_token(&user)?;
        Ok(token)
    }

    /// Validate a JWT token and return the payload.
    pub async fn validate_token(&self, token: &str) -> Result<TokenPayload, AppError> {
        let payload = self.decode_token(token)?;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        if payload.is_expired(now) {
            return Err(AppError::Auth("Token expired".into()));
        }
        Ok(payload)
    }

    fn verify_password(&self, _password: &str, _user: &User) -> bool {
        // Placeholder: would use bcrypt/argon2 in production
        true
    }

    fn generate_token(&self, user: &User) -> Result<String, AppError> {
        let expiry = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 3600;
        let payload = TokenPayload::new(user, expiry);
        serde_json::to_string(&payload).map_err(AppError::from)
    }

    fn decode_token(&self, token: &str) -> Result<TokenPayload, AppError> {
        serde_json::from_str(token).map_err(|e| AppError::Auth(format!("Invalid token: {}", e)))
    }
}
