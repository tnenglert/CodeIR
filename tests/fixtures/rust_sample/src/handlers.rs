use crate::models::{User, Validatable};
use crate::store::UserStore;
use crate::errors::AppError;

/// Handle a request to create a new user.
pub fn handle_create_user(
    store: &mut UserStore,
    username: String,
    email: String,
) -> Result<u64, AppError> {
    let user = User::new(0, username.clone(), email.clone());
    user.validate().map_err(|e| AppError::Validation(e))?;
    store.add_user(username, email)
}

/// Handle a request to fetch a user by ID.
pub fn handle_get_user(store: &UserStore, id: u64) -> Result<String, AppError> {
    match store.get_user(id) {
        Some(user) => user.to_json().map_err(|e| AppError::Serialization(e.to_string())),
        None => Err(AppError::NotFound(format!("User {} not found", id))),
    }
}

/// Handle a request to delete a user.
pub fn handle_delete_user(store: &mut UserStore, id: u64) -> Result<(), AppError> {
    match store.remove_user(id) {
        Some(_) => Ok(()),
        None => Err(AppError::NotFound(format!("User {} not found", id))),
    }
}

/// Handle a request to list all users as JSON.
pub async fn handle_list_users(store: &UserStore) -> Result<String, AppError> {
    let users = store.list_users();
    let mut results = Vec::new();
    for user in users {
        let json = user.to_json().map_err(|e| AppError::Serialization(e.to_string()))?;
        results.push(json);
    }
    Ok(format!("[{}]", results.join(",")))
}
