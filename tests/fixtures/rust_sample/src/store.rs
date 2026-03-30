use std::collections::HashMap;
use crate::models::User;
use crate::errors::AppError;

/// In-memory user store.
pub struct UserStore {
    users: HashMap<u64, User>,
    next_id: u64,
}

impl UserStore {
    /// Create an empty user store.
    pub fn new() -> Self {
        UserStore {
            users: HashMap::new(),
            next_id: 1,
        }
    }

    /// Add a user and return the assigned ID.
    pub fn add_user(&mut self, username: String, email: String) -> Result<u64, AppError> {
        let id = self.next_id;
        let user = User::new(id, username, email);
        self.users.insert(id, user);
        self.next_id += 1;
        Ok(id)
    }

    /// Look up a user by ID.
    pub fn get_user(&self, id: u64) -> Option<&User> {
        self.users.get(&id)
    }

    /// Remove a user by ID. Returns the removed user if found.
    pub fn remove_user(&mut self, id: u64) -> Option<User> {
        self.users.remove(&id)
    }

    /// List all users, sorted by ID.
    pub fn list_users(&self) -> Vec<&User> {
        let mut users: Vec<&User> = self.users.values().collect();
        users.sort_by_key(|u| u.id);
        users
    }

    /// Find users matching a predicate.
    pub fn find_users<F>(&self, predicate: F) -> Vec<&User>
    where
        F: Fn(&User) -> bool,
    {
        self.users.values().filter(|u| predicate(u)).collect()
    }
}
