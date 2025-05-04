import os
import subprocess
import sys
import copy

def main():
    """
    Prompts user for proxy settings and launches launch_camoufox.py
    in a new, detached process.
    """
    default_proxy = "http://127.0.0.1:7890"
    proxy_address = None
    enable_proxy = False

    # 1. Ask if proxy should be enabled
    while True:
        use_proxy_input = input(f"Enable proxy? (y/n, default n): ").strip().lower()
        if use_proxy_input == 'y':
            enable_proxy = True
            break
        elif use_proxy_input in ('n', ''):
            enable_proxy = False
            break
        else:
            print("Invalid input. Please enter 'y' or 'n'.")

    # 2. If proxy enabled, ask for address
    if enable_proxy:
        while True:
            use_default = input(f"Use default proxy address ({default_proxy})? (y/n, default y): ").strip().lower()
            if use_default in ('y', ''):
                proxy_address = default_proxy
                break
            elif use_default == 'n':
                custom_proxy = input("Enter custom proxy address (e.g., http://host:port): ").strip()
                if custom_proxy:
                    proxy_address = custom_proxy
                    break
                else:
                    print("Proxy address cannot be empty.")
            else:
                print("Invalid input. Please enter 'y' or 'n'.")
        print(f"Proxy enabled. Using address: {proxy_address}")
    else:
        print("Proxy disabled.")

    # 3. Prepare environment for the child process
    child_env = copy.deepcopy(os.environ)
    if proxy_address:
        child_env["HTTP_PROXY"] = proxy_address
        child_env["HTTPS_PROXY"] = proxy_address
        print(f"Setting HTTP_PROXY and HTTPS_PROXY to {proxy_address} for the new process.")
    else:
        # Ensure proxies are unset or empty in the child environment
        child_env.pop("HTTP_PROXY", None)
        child_env.pop("HTTPS_PROXY", None)
        print("Ensuring HTTP_PROXY and HTTPS_PROXY are not set for the new process.")


    # 4. Launch launch_camoufox.py in a new process
    script_to_run = "launch_camoufox.py"
    python_executable = sys.executable # Use the same python interpreter that ran this script

    print(f"\nStarting {script_to_run} in a new independent process...")

    try:
        if sys.platform == "win32":
            # On Windows, use CREATE_NEW_CONSOLE to launch in a new window, detached
            # DETACHED_PROCESS might also work but doesn't create a visible window usually
            # Popen without wait() already makes it non-blocking for the parent
            subprocess.Popen([python_executable, script_to_run],
                             env=child_env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            # On Linux/macOS, Popen starts a new process.
            # Closing the parent terminal might still send SIGHUP.
            # Using start_new_session=True creates a new process group, detaching it more reliably.
            subprocess.Popen([python_executable, script_to_run],
                             env=child_env,
                             start_new_session=True)

        print(f"{script_to_run} launched successfully in a separate process.")
        print("You can close this window now.")

    except FileNotFoundError:
        print(f"Error: Could not find '{script_to_run}'. Make sure it's in the same directory.")
    except Exception as e:
        print(f"An error occurred while launching the script: {e}")

    # Optional: Add a pause for the user in the initial script window if desired
    # input("Press Enter to exit this launcher script...")

if __name__ == "__main__":
    main()