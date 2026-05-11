#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    ffi::c_void,
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    ptr::{null, null_mut},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};

use tauri::{AppHandle, Manager};

const BACKEND_HOST: &str = "localhost";
const BACKEND_PORT: u16 = 3978;
const AZURE_OPENAI_API_KEY_SECRET_FILE: &str = "azure-openai-api-key.bin";

struct BackendProcess(Mutex<Option<Child>>);

struct BackendLaunch {
    command: PathBuf,
    args: Vec<String>,
    current_dir: PathBuf,
    hands_command: Option<PathBuf>,
    runtime_dir: PathBuf,
    log_dir: PathBuf,
}

#[repr(C)]
struct DataBlob {
    cb_data: u32,
    pb_data: *mut u8,
}

#[cfg(target_os = "windows")]
#[link(name = "Crypt32")]
unsafe extern "system" {
    fn CryptProtectData(
        p_data_in: *const DataBlob,
        sz_data_descr: *const u16,
        p_optional_entropy: *const DataBlob,
        pv_reserved: *mut c_void,
        p_prompt_struct: *mut c_void,
        dw_flags: u32,
        p_data_out: *mut DataBlob,
    ) -> i32;
    fn CryptUnprotectData(
        p_data_in: *const DataBlob,
        ppsz_data_descr: *mut *mut u16,
        p_optional_entropy: *const DataBlob,
        pv_reserved: *mut c_void,
        p_prompt_struct: *mut c_void,
        dw_flags: u32,
        p_data_out: *mut DataBlob,
    ) -> i32;
}

#[cfg(target_os = "windows")]
#[link(name = "Kernel32")]
unsafe extern "system" {
    fn LocalFree(h_mem: *mut c_void) -> *mut c_void;
    fn GetLastError() -> u32;
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        let Ok(mut child_guard) = self.0.lock() else {
            return;
        };
        let Some(mut child) = child_guard.take() else {
            return;
        };

        if let Ok(None) = child.try_wait() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn backend_is_running() -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("{BACKEND_HOST}:{BACKEND_PORT}")
            .parse()
            .unwrap_or_else(|_| ([127, 0, 0, 1], BACKEND_PORT).into()),
        Duration::from_millis(300),
    ) else {
        return false;
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(700)));
    let request = format!(
        "GET /api/health HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    response.starts_with("HTTP/1.1 200") && response.contains("\"status\": \"ok\"")
}

fn repository_root() -> Option<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.parent()?.parent().map(Path::to_path_buf)
}

fn python_interpreter(repo_root: &Path) -> PathBuf {
    let venv_python = if cfg!(target_os = "windows") {
        repo_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join(".venv").join("bin").join("python")
    };

    if venv_python.exists() {
        return venv_python;
    }

    if cfg!(target_os = "windows") {
        PathBuf::from("python")
    } else {
        PathBuf::from("python3")
    }
}

fn append_log(path: &Path) -> Option<Stdio> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .ok()
        .map(Stdio::from)
}

fn secure_storage_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("could not resolve AzulClaw app data directory: {error}"))?;
    Ok(app_data_dir.join("secure"))
}

fn azure_openai_api_key_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(secure_storage_dir(app)?.join(AZURE_OPENAI_API_KEY_SECRET_FILE))
}

#[cfg(target_os = "windows")]
fn dpapi_protect_bytes(plaintext: &[u8]) -> Result<Vec<u8>, String> {
    let input = DataBlob {
        cb_data: plaintext.len() as u32,
        pb_data: plaintext.as_ptr() as *mut u8,
    };
    let mut output = DataBlob {
        cb_data: 0,
        pb_data: null_mut(),
    };

    let ok = unsafe {
        CryptProtectData(
            &input,
            null(),
            null(),
            null_mut(),
            null_mut(),
            0,
            &mut output,
        )
    };
    if ok == 0 {
        let code = unsafe { GetLastError() };
        return Err(format!("DPAPI encryption failed with Windows error {code}"));
    }

    let bytes = unsafe { std::slice::from_raw_parts(output.pb_data, output.cb_data as usize) }.to_vec();
    unsafe {
        let _ = LocalFree(output.pb_data as *mut c_void);
    }
    Ok(bytes)
}

#[cfg(target_os = "windows")]
fn dpapi_unprotect_bytes(ciphertext: &[u8]) -> Result<Vec<u8>, String> {
    let input = DataBlob {
        cb_data: ciphertext.len() as u32,
        pb_data: ciphertext.as_ptr() as *mut u8,
    };
    let mut output = DataBlob {
        cb_data: 0,
        pb_data: null_mut(),
    };
    let mut description: *mut u16 = null_mut();

    let ok = unsafe {
        CryptUnprotectData(
            &input,
            &mut description,
            null(),
            null_mut(),
            null_mut(),
            0,
            &mut output,
        )
    };
    if ok == 0 {
        let code = unsafe { GetLastError() };
        return Err(format!("DPAPI decryption failed with Windows error {code}"));
    }

    let bytes = unsafe { std::slice::from_raw_parts(output.pb_data, output.cb_data as usize) }.to_vec();
    unsafe {
        if !description.is_null() {
            let _ = LocalFree(description as *mut c_void);
        }
        let _ = LocalFree(output.pb_data as *mut c_void);
    }
    Ok(bytes)
}

#[cfg(not(target_os = "windows"))]
fn dpapi_protect_bytes(plaintext: &[u8]) -> Result<Vec<u8>, String> {
    let _ = plaintext;
    Err("secure Azure OpenAI API key persistence is only implemented on Windows desktop builds".to_string())
}

#[cfg(not(target_os = "windows"))]
fn dpapi_unprotect_bytes(ciphertext: &[u8]) -> Result<Vec<u8>, String> {
    let _ = ciphertext;
    Err("secure Azure OpenAI API key persistence is only implemented on Windows desktop builds".to_string())
}

fn store_azure_openai_api_key_secret(app: &AppHandle, secret: &str) -> Result<(), String> {
    let path = azure_openai_api_key_path(app)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("could not create secure storage directory: {error}"))?;
    }
    let encrypted = dpapi_protect_bytes(secret.as_bytes())?;
    fs::write(&path, encrypted).map_err(|error| format!("could not store secure secret: {error}"))
}

fn load_azure_openai_api_key_secret(app: &AppHandle) -> Result<Option<String>, String> {
    let path = azure_openai_api_key_path(app)?;
    if !path.exists() {
        return Ok(None);
    }
    let encrypted = fs::read(&path).map_err(|error| format!("could not read secure secret: {error}"))?;
    let decrypted = dpapi_unprotect_bytes(&encrypted)?;
    let secret = String::from_utf8(decrypted)
        .map_err(|error| format!("secure secret payload is not valid UTF-8: {error}"))?;
    let trimmed = secret.trim().to_string();
    if trimmed.is_empty() {
        return Ok(None);
    }
    Ok(Some(trimmed))
}

fn clear_azure_openai_api_key_secret(app: &AppHandle) -> Result<(), String> {
    let path = azure_openai_api_key_path(app)?;
    if path.exists() {
        fs::remove_file(path).map_err(|error| format!("could not remove secure secret: {error}"))?;
    }
    Ok(())
}

#[tauri::command]
fn store_azure_openai_api_key(app: AppHandle, secret: String) -> Result<(), String> {
    let trimmed = secret.trim();
    if trimmed.is_empty() {
        return Err("secret must not be empty".to_string());
    }
    store_azure_openai_api_key_secret(&app, trimmed)
}

#[tauri::command]
fn load_azure_openai_api_key(app: AppHandle) -> Result<Option<String>, String> {
    load_azure_openai_api_key_secret(&app)
}

#[tauri::command]
fn has_azure_openai_api_key(app: AppHandle) -> Result<bool, String> {
    Ok(load_azure_openai_api_key_secret(&app)?.is_some())
}

#[tauri::command]
fn clear_azure_openai_api_key(app: AppHandle) -> Result<(), String> {
    clear_azure_openai_api_key_secret(&app)
}

#[tauri::command]
fn is_azure_openai_api_key_storage_available() -> bool {
    cfg!(target_os = "windows")
}

fn append_text_log(path: &Path, message: &str) {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{message}");
    }
}

fn autostart_error_log_path(app: &tauri::App) -> Option<PathBuf> {
    app.path()
        .app_data_dir()
        .ok()
        .map(|dir| dir.join("logs").join("desktop-backend.err.log"))
}

fn record_backend_autostart_error(app: &tauri::App, error: &str) {
    eprintln!("AzulClaw backend autostart failed: {error}");
    if let Some(log_path) = autostart_error_log_path(app) {
        append_text_log(
            &log_path,
            &format!("AzulClaw backend autostart failed before backend logging was available: {error}"),
        );
    }
}

fn executable_name(name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn packaged_backend_launch(app: &tauri::App) -> Result<Option<BackendLaunch>, String> {
    let Ok(resource_dir) = app.path().resource_dir() else {
        return Ok(None);
    };

    let backend_root = resource_dir.join("backend");
    if !cfg!(debug_assertions) && !backend_root.exists() {
        return Err(format!(
            "packaged backend resources are missing at {}",
            backend_root.display()
        ));
    }

    let backend_exe = backend_root
        .join("azul-backend")
        .join(executable_name("azul-backend"));

    if !backend_exe.exists() {
        if !cfg!(debug_assertions) {
            return Err(format!(
                "packaged backend executable is missing at {}",
                backend_exe.display()
            ));
        }
        return Ok(None);
    }

    let hands_exe = backend_root
        .join("azul-hands-mcp")
        .join(executable_name("azul-hands-mcp"));
    if !hands_exe.exists() {
        return Err(format!(
            "packaged backend found, but MCP executable is missing at {}",
            hands_exe.display()
        ));
    }

    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("could not resolve AzulClaw app data directory: {error}"))?;
    let runtime_dir = app_data_dir.join("runtime");
    let log_dir = app_data_dir.join("logs");

    let current_dir = backend_exe
        .parent()
        .ok_or("could not resolve packaged backend directory")?
        .to_path_buf();

    Ok(Some(BackendLaunch {
        command: backend_exe,
        args: Vec::new(),
        current_dir,
        hands_command: Some(hands_exe),
        runtime_dir,
        log_dir,
    }))
}

fn development_backend_launch() -> Result<BackendLaunch, String> {
    let repo_root = repository_root().ok_or("could not resolve repository root")?;
    Ok(BackendLaunch {
        command: python_interpreter(&repo_root),
        args: vec![
            "-m".to_string(),
            "azul_backend.azul_brain.main_launcher".to_string(),
        ],
        current_dir: repo_root.clone(),
        hands_command: None,
        runtime_dir: repo_root.join("memory"),
        log_dir: repo_root.join("memory").join("runtime-logs"),
    })
}

fn backend_launch(app: &tauri::App) -> Result<BackendLaunch, String> {
    if cfg!(debug_assertions) {
        return development_backend_launch();
    }
    if let Some(launch) = packaged_backend_launch(app)? {
        return Ok(launch);
    }
    development_backend_launch()
}

fn start_backend(app: &tauri::App) -> Result<Option<Child>, String> {
    if backend_is_running() {
        eprintln!(
            "AzulClaw backend already reachable on http://localhost:{BACKEND_PORT}; reusing it."
        );
        return Ok(None);
    }

    let launch = backend_launch(app)?;
    fs::create_dir_all(&launch.log_dir)
        .map_err(|error| format!("could not create backend log directory: {error}"))?;
    fs::create_dir_all(&launch.runtime_dir)
        .map_err(|error| format!("could not create backend runtime directory: {error}"))?;

    let mut command = Command::new(&launch.command);
    command
        .args(&launch.args)
        .current_dir(&launch.current_dir)
        .env("PORT", BACKEND_PORT.to_string())
        .env("AZUL_RUNTIME_DIR", &launch.runtime_dir)
        .env("AZUL_BACKEND_LOG_DIR", &launch.log_dir)
        .stdout(
            append_log(&launch.log_dir.join("desktop-backend.out.log")).unwrap_or(Stdio::null()),
        )
        .stderr(
            append_log(&launch.log_dir.join("desktop-backend.err.log")).unwrap_or(Stdio::null()),
        );

    if let Some(hands_command) = &launch.hands_command {
        command.env("AZUL_HANDS_MCP_COMMAND", hands_command);
    }
    match load_azure_openai_api_key_secret(&app.handle()) {
        Ok(Some(api_key)) => {
            command.env("AZURE_OPENAI_API_KEY", api_key);
        }
        Ok(None) => {}
        Err(error) => {
            append_text_log(
                &launch.log_dir.join("desktop-backend.err.log"),
                &format!("Azure OpenAI secure secret could not be loaded: {error}"),
            );
            let _ = clear_azure_openai_api_key_secret(&app.handle());
        }
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    command
        .spawn()
        .map(Some)
        .map_err(|error| format!("could not start AzulClaw backend: {error}"))
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            match start_backend(app) {
                Ok(child) => {
                    app.manage(BackendProcess(Mutex::new(child)));
                }
                Err(error) => {
                    record_backend_autostart_error(app, &error);
                    app.manage(BackendProcess(Mutex::new(None)));
                }
            }
            Ok(())
        })
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            store_azure_openai_api_key,
            load_azure_openai_api_key,
            has_azure_openai_api_key,
            clear_azure_openai_api_key,
            is_azure_openai_api_key_storage_available
        ])
        .run(tauri::generate_context!())
        .expect("error while running AzulClaw desktop");
}
