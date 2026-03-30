use crate::models::User;
use crate::handlers::api;

mod models;
mod handlers;
mod utils;

const VERSION: &str = "0.1.0";

fn main() {
    let config = utils::config::load_config();
    let user = User::new("Alice".to_string(), 30);
    println!("Starting {} v{}", config.app_name, VERSION);
    api::start_server(config);
}

async fn shutdown_signal() {
    tokio::signal::ctrl_c().await.expect("failed to listen");
    println!("Shutting down...");
}
