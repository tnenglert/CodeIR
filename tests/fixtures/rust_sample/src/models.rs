use serde::{Deserialize, Serialize};

/// User roles in the system.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum UserRole {
    Admin,
    Editor,
    Viewer,
}

/// A user in the system.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: i64,
    pub username: String,
    pub email: String,
    pub role: UserRole,
    pub active: bool,
}

impl User {
    pub fn new(id: i64, username: String, email: String) -> Self {
        Self {
            id,
            username,
            email,
            role: UserRole::Viewer,
            active: true,
        }
    }

    pub fn is_admin(&self) -> bool {
        self.role == UserRole::Admin
    }

    pub fn deactivate(&mut self) {
        self.active = false;
    }

    pub fn promote(&mut self, new_role: UserRole) -> Result<(), String> {
        if !self.active {
            return Err("Cannot promote inactive user".to_string());
        }
        self.role = new_role;
        Ok(())
    }
}

/// Token payload for JWT authentication.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenPayload {
    pub sub: String,
    pub exp: u64,
    pub role: UserRole,
}

impl TokenPayload {
    pub fn new(user: &User, expiry: u64) -> Self {
        Self {
            sub: user.username.clone(),
            exp: expiry,
            role: user.role.clone(),
        }
    }

    pub fn is_expired(&self, now: u64) -> bool {
        now >= self.exp
    }
}

/// Trait for entities that can be serialized to JSON.
pub trait JsonSerializable {
    fn to_json(&self) -> Result<String, serde_json::Error>;
    fn from_json(json: &str) -> Result<Self, serde_json::Error>
    where
        Self: Sized;
}

impl JsonSerializable for User {
    fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string(self)
    }

    fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_user_defaults() {
        let user = User::new(1, "alice".into(), "alice@example.com".into());
        assert_eq!(user.role, UserRole::Viewer);
        assert!(user.active);
    }

    #[test]
    fn test_promote_inactive_user_fails() {
        let mut user = User::new(1, "bob".into(), "bob@example.com".into());
        user.deactivate();
        assert!(user.promote(UserRole::Admin).is_err());
    }
}
