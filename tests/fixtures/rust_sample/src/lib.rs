//! Sample Rust library for CodeIR testing.

pub mod models;
pub mod store;
pub mod handlers;
pub mod errors;
pub mod config;

use crate::config::AppConfig;
use crate::errors::AppError;

/// Initialize the application with the given configuration.
pub fn init_app(config: AppConfig) -> Result<(), AppError> {
    if config.debug {
        println!("Debug mode enabled");
    }
    Ok(())
}
