use serde::{Deserialize, Serialize};
use std::fmt;

/// Maximum allowed username length.
const MAX_USERNAME_LEN: usize = 64;

/// A registered user in the system.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: u64,
    pub username: String,
    pub email: String,
    pub role: Role,
}

/// User roles with ascending privilege levels.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum Role {
    Guest,
    Member,
    Admin,
}

/// Trait for entities that can be validated before persistence.
pub trait Validatable {
    fn validate(&self) -> Result<(), String>;
}

impl User {
    /// Create a new user with the given details.
    pub fn new(id: u64, username: String, email: String) -> Self {
        User {
            id,
            username,
            email,
            role: Role::Member,
        }
    }

    /// Promote a user to admin role.
    pub fn promote(&mut self) {
        self.role = Role::Admin;
    }

    /// Check whether the user has admin privileges.
    pub fn is_admin(&self) -> bool {
        self.role == Role::Admin
    }

    /// Serialize the user to a JSON string.
    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string(self)
    }
}

impl Validatable for User {
    fn validate(&self) -> Result<(), String> {
        if self.username.is_empty() {
            return Err("Username cannot be empty".into());
        }
        if self.username.len() > MAX_USERNAME_LEN {
            return Err(format!("Username exceeds {} characters", MAX_USERNAME_LEN));
        }
        if !self.email.contains('@') {
            return Err("Invalid email address".into());
        }
        Ok(())
    }
}

impl fmt::Display for User {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}({})", self.username, self.email)
    }
}
