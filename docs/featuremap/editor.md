| Shape properties: border colour/size | editor/shapes.py, editor/manager.py | AnnotationOptions load/save per ShareX targets; toolbar setters | done | tests/test_editor_manager.py::test_toolbar_setter_updates_selected_shape |
| BorderStyle Solid/Dash/Dot/DashDot/DashDotDot | editor/render.py | cairo set_dash with GDI+ patterns x pen width | done | tests/test_editor_shapes.py::test_dash_pattern_has_gaps |
| Fill colour | editor/render.py | cairo fill before stroke | done | tests/test_editor_shapes.py::test_rectangle_fill_color |
| Shadow | editor/render.py | shape redrawn in the shadow colour at the shadow offset first | done | tests/test_editor_shapes.py::test_shadow_is_drawn_offset |
| Corner radius | editor/render.py | rounded path (capsule at half the short side) | done | tests/test_editor_shapes.py::test_rectangle_corner_radius_rounds_the_corner |
| Font | editor/render.py, editor/manager.py | Pango font description (family, size pt @96 dpi, bold, italic, underline attr) | done | tests/test_editor_shapes.py::test_text_background_box_and_text |
| Arrow heads | editor/render.py | End/Start/Both (Both only when longer than 12 x width) | done | tests/test_editor_shapes.py::test_arrow_head_pixels |
| Draggable line centre points | editor/shapes.py, editor/manager.py | line_center_point_count nodes, drag bends into a curve | done | tests/test_editor_shapes.py::test_line_center_point_drag_bends_line |
| Select/move/resize handles | editor/manager.py, editor/shapes.py | 8 nodes (BottomRight first), Shift = proportional, line point nodes | done | tests/test_editor_manager.py::test_resize_handles |
| Copy, paste, duplicate | editor/manager.py | Ctrl+C/V internal shape clipboard, Ctrl+V system clipboard via read_clipboard, Ctrl+D at pointer | done | tests/test_editor_manager.py::test_copy_paste_shape |
| Z-order | editor/manager.py | Home/End/PageUp/PageDown move in the shape list | done | tests/test_editor_manager.py::test_z_order_and_keys |
| Undo/redo | editor/manager.py | snapshot mementos (shape clones + immutable canvas ref) for every mutation | done | tests/test_editor_manager.py::test_undo_redo_restores_exact_state |
| Crop | editor/manager.py | crop_area moves shapes by -origin | done | tests/test_editor_ops.py::test_crop_moves_shapes_with_the_image |
| Cut-out | editor/manager.py, editor/render.py | cut_out_middle + edge effect, shapes collapse | done | tests/test_editor_ops.py::test_cut_out_rows_and_shape_collapse |
| Resize image | editor/manager.py | Pillow resize with the interpolation option | done | tests/test_editor_ops.py::test_resize_image |
| Resize canvas | editor/manager.py | margins (negative trims) in a colour; shapes move with content | done | tests/test_editor_ops.py::test_resize_canvas_adds_coloured_margins_and_moves_shapes |
| ImageInsertMethod | editor/manager.py | Center / CanvasExpandDown / CanvasExpandRight | done | tests/test_editor_ops.py::test_insert_image_expand_down |
| Rotate | editor/manager.py | Pillow transpose 90/180/270 | done | tests/test_editor_ops.py::test_rotate |
| Flip | editor/manager.py | Pillow transpose horizontal/vertical | done | tests/test_editor_ops.py::test_flip |
| Auto crop | editor/manager.py, editor/render.py | FindAutoCropRectangle port; opens a crop tool to confirm | done | tests/test_editor_ops.py::test_auto_crop_trims_uniform_border |
| Insert image from file | editor/manager.py | insert_image_file -> DrawingImage | done | tests/test_editor_ops.py::test_insert_image_file_and_from_screen |
| Insert image from screen | editor/manager.py | capture_screen_image callback -> DrawingImage | done | tests/test_editor_ops.py::test_insert_image_file_and_from_screen |
| Add image effects | editor/manager.py | apply_effects callback replaces the canvas (undoable) | done | tests/test_editor_ops.py::test_add_image_effects_callback |
| ImageEditorStartMode | src/sharel/ui/windows/editor.py | EditorWindow._apply_start_mode (AutoSize/Normal/Maximized/Fullscreen/PreviousState) | done | tests/test_ui_editor_window.py::test_normal_and_auto_size; tests/test_ui_editor_window.py::test_maximized_and_fullscreen; tests/test_ui_editor_window.py::test_previous_state_restores_the_last_size_and_maximized |
| Save | editor/manager.py | Ctrl+S -> save_image(render_output()) | done | tests/test_editor_ops.py::test_editor_save_copy_upload_print_shortcuts; tests/test_ui_editor_window.py::test_save_save_as_copy_upload_print_from_the_editor_window |
| Save as | editor/manager.py | Ctrl+Shift+S -> save_image_as(render_output()) | done | tests/test_editor_ops.py::test_editor_save_copy_upload_print_shortcuts; tests/test_ui_editor_window.py::test_save_save_as_copy_upload_print_from_the_editor_window |
| Copy | editor/manager.py | Ctrl+C -> copy_image(render_output()); auto copy on modify | done | tests/test_editor_ops.py::test_editor_save_copy_upload_print_shortcuts; tests/test_editor_ops.py::test_editor_auto_copy_on_modify; tests/test_ui_editor_window.py::test_save_save_as_copy_upload_print_from_the_editor_window |
| Upload | editor/manager.py | Ctrl+U -> upload_image(render_output()) | done | tests/test_editor_ops.py::test_editor_save_copy_upload_print_shortcuts; tests/test_ui_editor_window.py::test_save_save_as_copy_upload_print_from_the_editor_window |
| Print | editor/manager.py | Ctrl+P -> print_image(render_output()) | done | tests/test_editor_ops.py::test_editor_save_copy_upload_print_shortcuts; tests/test_ui_editor_window.py::test_save_save_as_copy_upload_print_from_the_editor_window |
| Step: numbers, letters, roman numerals | editor/shapes.py | NumberToLetters / NumberToRomanNumeral ports, StepType | done | tests/test_editor_shapes.py::test_step_labels |
| Sticker pack bundled | data/stickers/Twemoji, editor/stickers.py | 72 Twemoji 72x72 PNGs (CC-BY 4.0) with LICENSE/ATTRIBUTION | done | tests/test_editor_shapes.py::test_sticker_from_pack |
