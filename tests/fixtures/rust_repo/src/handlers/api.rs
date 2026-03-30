use crate::models::{AppError, User};
use crate::models::user::Validatable;
use crate::utils::config::Config;

/// Start the API server with the given configuration.
pub fn start_server(config: Config) {
    println!("Server starting on port {}", config.port);
    // In a real app this would start an HTTP server
}

/// Look up a user by name from a list.
pub fn find_user(users: &[User], name: &str) -> Result<User, AppError> {
    for user in users {
        if user.name == name {
            return Ok(user.clone());
        }
    }
    Err(AppError::NotFound(format!("user '{}' not found", name)))
}

/// Create a new user after validation.
pub fn create_user(name: String, age: u32) -> Result<User, AppError> {
    let user = User::new(name, age);
    user.validate()?;
    Ok(user)
}

/// Fetch user data from a remote service.
pub async fn fetch_remote_user(url: &str) -> Result<User, AppError> {
    let client = reqwest::Client::new();
    let response = client.get(url).send().await
        .map_err(|e| AppError::Internal(e.to_string()))?;

    let text = response.text().await
        .map_err(|e| AppError::Internal(e.to_string()))?;

    let user: User = serde_json::from_str(&text)
        .map_err(|e| AppError::Validation(e.to_string()))?;

    user.validate()?;
    Ok(user)
}

/// Delete a user by name (returns whether deletion occurred).
pub fn delete_user(users: &mut Vec<User>, name: &str) -> bool {
    let initial_len = users.len();
    users.retain(|u| u.name != name);
    users.len() < initial_len
}

const MAX_USERS: usize = 1000;

pub fn check_capacity(users: &[User]) -> Result<(), AppError> {
    if users.len() >= MAX_USERS {
        return Err(AppError::Internal("user capacity exceeded".to_string()));
    }
    Ok(())
}
