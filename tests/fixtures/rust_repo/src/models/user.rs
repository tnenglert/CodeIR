use serde::{Deserialize, Serialize};
use crate::models::AppError;

/// A user in the system.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub name: String,
    pub age: u32,
    pub email: Option<String>,
}

/// Role assigned to a user.
#[derive(Debug, Clone, PartialEq)]
pub enum UserRole {
    Admin,
    Editor,
    Viewer,
}

/// Trait for entities that can be validated.
pub trait Validatable {
    fn validate(&self) -> Result<(), AppError>;
}

impl User {
    pub fn new(name: String, age: u32) -> Self {
        User {
            name,
            age,
            email: None,
        }
    }

    pub fn with_email(mut self, email: String) -> Self {
        self.email = Some(email);
        self
    }

    pub fn display_name(&self) -> String {
        match &self.email {
            Some(email) => format!("{} <{}>", self.name, email),
            None => self.name.clone(),
        }
    }

    pub fn is_adult(&self) -> bool {
        self.age >= 18
    }
}

impl Validatable for User {
    fn validate(&self) -> Result<(), AppError> {
        if self.name.is_empty() {
            return Err(AppError::Validation("name cannot be empty".to_string()));
        }
        if self.age > 150 {
            return Err(AppError::Validation("invalid age".to_string()));
        }
        Ok(())
    }
}

impl UserRole {
    pub fn can_edit(&self) -> bool {
        matches!(self, UserRole::Admin | UserRole::Editor)
    }

    pub fn from_str(s: &str) -> Result<Self, AppError> {
        match s.to_lowercase().as_str() {
            "admin" => Ok(UserRole::Admin),
            "editor" => Ok(UserRole::Editor),
            "viewer" => Ok(UserRole::Viewer),
            _ => Err(AppError::Validation(format!("unknown role: {}", s))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_user_new() {
        let user = User::new("Bob".to_string(), 25);
        assert_eq!(user.name, "Bob");
        assert_eq!(user.age, 25);
        assert!(user.email.is_none());
    }

    #[test]
    fn test_user_validation() {
        let user = User::new("".to_string(), 25);
        assert!(user.validate().is_err());
    }

    #[test]
    fn test_display_name() {
        let user = User::new("Alice".to_string(), 30)
            .with_email("alice@example.com".to_string());
        assert!(user.display_name().contains("alice@example.com"));
    }

    #[test]
    fn test_role_from_str() {
        assert_eq!(UserRole::from_str("admin").unwrap(), UserRole::Admin);
        assert!(UserRole::from_str("unknown").is_err());
    }
}
