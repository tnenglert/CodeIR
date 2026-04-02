pub mod config;
pub mod db;
pub mod errors;
pub mod handlers;
pub mod models;

/// Re-export commonly used types
pub use config::AppConfig;
pub use errors::AppError;
pub use models::{User, UserRole};
