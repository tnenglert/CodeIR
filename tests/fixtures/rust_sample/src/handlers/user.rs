use crate::db::Database;
use crate::errors::AppError;
use crate::models::{User, UserRole};

/// Handles user-related operations.
pub struct UserHandler {
    db: Database,
}

impl UserHandler {
    pub fn new(db: Database) -> Self {
        Self { db }
    }

    /// List all active users.
    pub async fn list_users(&self) -> Result<Vec<User>, AppError> {
        let users = self.db.query_users().await?;
        let active: Vec<User> = users.into_iter().filter(|u| u.active).collect();
        Ok(active)
    }

    /// Get a single user by ID.
    pub async fn get_user(&self, id: i64) -> Result<User, AppError> {
        self.db.find_user(id).await
    }

    /// Create a new user with the given details.
    pub async fn create_user(
        &self,
        username: String,
        email: String,
    ) -> Result<User, AppError> {
        if username.is_empty() {
            return Err(AppError::Auth("Username cannot be empty".into()));
        }
        let user = User::new(0, username, email);
        self.db.insert_user(&user).await?;
        Ok(user)
    }

    /// Update a user's role. Only admins can promote to Admin.
    pub async fn update_role(
        &self,
        user_id: i64,
        new_role: UserRole,
        requester: &User,
    ) -> Result<(), AppError> {
        if !requester.is_admin() {
            return Err(AppError::Auth("Only admins can change roles".into()));
        }
        let mut user = self.db.find_user(user_id).await?;
        user.promote(new_role)
            .map_err(|e| AppError::Auth(e))?;
        self.db.update_user(&user).await
    }

    /// Delete a user by ID.
    pub async fn delete_user(&self, id: i64) -> Result<(), AppError> {
        self.db.delete_user(id).await
    }
}
