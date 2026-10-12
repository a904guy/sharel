| install.sh / uninstall.sh | install.sh | XDG user install with manifest; uninstall removes it | done | tests/test_packaging.py::test_install_and_uninstall_dev_mode |
| .deb package | packaging/build-deb.sh | dpkg-deb | done | tests/test_packaging.py::test_deb_contents |
| Desktop entry + icon | data/applications | XDG desktop entry, hicolor icons, desktop actions | done | tests/test_packaging_data.py::test_desktop_entries_validate_and_point_at_sharel; tests/test_packaging.py::test_install_and_uninstall_dev_mode |
| Autostart | src/sharel/core/autostart.py | XDG autostart entry | done | tests/test_ui_settings.py::test_app_settings_autostart_tray_language |
| Nautilus integration | data/nautilus | Nautilus scripts | done | tests/test_packaging_data.py::test_nautilus_scripts_pass_the_selection_to_sharel; tests/test_packaging.py::test_install_and_uninstall_dev_mode |
| Native messaging host | src/sharel/tools/native_host.py | Chrome/Firefox manifests, stdio framing, app.native GAction | done | tests/test_packaging_data.py::test_native_host_framing_and_validation; tests/test_packaging_data.py::test_native_host_process_reaches_the_running_app; tests/test_packaging.py::test_install_and_uninstall_dev_mode |
| GSettings schema | data/glib-2.0 | glib-compile-schemas | done | tests/test_packaging_data.py::test_gsettings_schema_compiles_and_has_every_key_the_code_reads; tests/test_packaging_data.py::test_main_window_geometry_goes_through_gsettings |
| README | README.md | - | planned | - |
