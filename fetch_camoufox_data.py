import ssl
import sys
# --- WARNING: Monkey patching SSL for fetching data ONLY ---
# --- This disables certificate verification globally for this script execution ---
# --- Do NOT use this approach for general network requests ---
print("WARNING: Temporarily disabling SSL certificate verification to fetch Camoufox data...")
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    # Legacy Python versions might not have _create_unverified_context
    print("ERROR: Cannot disable SSL verification on this Python version.")
    sys.exit(1)
else:
    # Monkey patch the default context creation
    ssl._create_default_https_context = _create_unverified_https_context
    print("SSL verification temporarily disabled.")

# Now, try to import and run the fetch command logic from camoufox
print("Attempting to run Camoufox fetch logic...")
try:
    # The exact way to trigger fetch programmatically might differ.
    # This tries to import the CLI module and run the fetch command.
    from camoufox import cli
    # Simulate command line arguments: ['fetch']
    cli.cli(['fetch'])
    print("Camoufox fetch process finished.")
except ImportError:
    print("\nERROR: Could not import camoufox.cli. Make sure camoufox package was installed (even partially).")
    print("Try running: python3 -m pip list | grep camoufox")
except FileNotFoundError as e:
     print(f"\nERROR during fetch (likely download failed again despite disabled SSL?): {e}")
     print("Please check network connectivity and permissions.")
except Exception as e:
    print(f"\nERROR running camoufox fetch programmatically: {e}")
    import traceback
    traceback.print_exc()

print("Script finished. SSL verification will be enabled again on next Python run.")
