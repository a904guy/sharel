| RegionRectangle | editor/shapes.py, editor/manager.py | drag; quick crop; union bounds, mask only when not a plain rect | done | tests/test_editor_manager.py::test_quick_crop_rectangle_region |
| RegionEllipse | editor/shapes.py, editor/manager.py | cairo ellipse path -> A8 mask of the region bounds | done | tests/test_editor_manager.py::test_ellipse_region_mask |
| RegionFreehand | editor/shapes.py, editor/manager.py | point list (Shift = polygon mode) -> polygon mask | done | tests/test_editor_manager.py::test_freehand_region_mask |
| ToolSelect | editor/manager.py | hit test top-down, move by pointer delta, 8 resize nodes | done | tests/test_editor_manager.py::test_select_tool_moves_shape_exactly |
| DrawingRectangle | editor/shapes.py, editor/render.py | cairo path (GDI rect - 1 px), dash/fill/shadow/corner radius | done | tests/test_editor_shapes.py::test_rectangle_border_and_no_fill |
| DrawingEllipse | editor/shapes.py, editor/render.py | cairo ellipse in the shape rect | done | tests/test_editor_shapes.py::test_ellipse_outline |
| DrawingFreehand | editor/shapes.py, editor/render.py | polyline, round caps/joins | done | tests/test_editor_shapes.py::test_freehand_follows_pointer |
| DrawingFreehandArrow | editor/shapes.py, editor/render.py | polyline trimmed by BaseInset + GDI CustomLineCap arrow head | done | tests/test_editor_shapes.py::test_freehand_arrow_has_head |
| DrawingLine | editor/shapes.py, editor/render.py | 2 + centre points; dragged centre node -> cardinal spline (tension 0.5) | done | tests/test_editor_shapes.py::test_line_center_point_drag_bends_line |
| DrawingArrow | editor/shapes.py, editor/render.py | ShareX arrow cap (6w long, 4w wide), End/Start/Both | done | tests/test_editor_shapes.py::test_arrow_both_heads |
| DrawingTextOutline | editor/shapes.py, editor/render.py | PangoCairo.layout_path stroked (round join) then filled, optional gradient | done | tests/test_editor_shapes.py::test_text_outline_draws_text_and_outline |
| DrawingTextBackground | editor/shapes.py, editor/render.py | rounded rect + Pango layout aligned in the box; auto size text + (15, 20) | done | tests/test_editor_shapes.py::test_text_background_box_and_text |
| DrawingSpeechBalloon | editor/shapes.py, editor/render.py | box + triangular tail to the draggable Extra node | done | tests/test_editor_shapes.py::test_speech_balloon_tail |
| DrawingStep | editor/shapes.py, editor/render.py | circle sized to the measured label, renumbered in z-order from the first-step value | done | tests/test_editor_shapes.py::test_step_numbers_auto_increment_and_draw |
| DrawingMagnify | editor/shapes.py, editor/render.py | ellipse clip, canvas source scaled by strength/100 with the chosen filter | done | tests/test_editor_shapes.py::test_magnify_enlarges_underlying_image |
| DrawingImage | editor/shapes.py, editor/manager.py | request_image_file callback (Ctrl = last file), image centred on the click | done | tests/test_editor_shapes.py::test_image_from_file |
| DrawingImageScreen | editor/shapes.py, editor/manager.py | dragged area of the canvas becomes a movable image | done | tests/test_editor_shapes.py::test_image_screen_copies_area_and_moves |
| DrawingSticker | editor/shapes.py, editor/stickers.py | request_sticker callback; bundled Twemoji pack, limited to sticker_size | done | tests/test_editor_shapes.py::test_sticker_from_pack |
| DrawingCursor | editor/shapes.py, editor/render.py | drawn arrow cursor bitmap or cursor_image callback, hotspot on the click | done | tests/test_editor_shapes.py::test_cursor_default_arrow_bitmap |
| DrawingSmartEraser | editor/shapes.py, editor/manager.py | rect filled with the canvas colour under the start point (dimmed colour on screen) | done | tests/test_editor_shapes.py::test_smart_eraser_fills_with_start_colour |
| EffectBlur | editor/shapes.py, editor/render.py | numpy box blur (odd range, H/V twice), cached preview | done | tests/test_editor_shapes.py::test_blur_reduces_variance_only_inside |
| EffectPixelate | editor/shapes.py, editor/render.py | alpha-weighted block average anchored at the shape origin | done | tests/test_editor_shapes.py::test_pixelate_makes_uniform_blocks |
| EffectHighlight | editor/shapes.py, editor/render.py | per channel min(pixel, highlight colour) like ImageHelpers.HighlightImage | done | tests/test_editor_shapes.py::test_highlight_tints_like_sharex_min |
| ToolCrop | editor/shapes.py, editor/manager.py | canvas-clamped rect with confirm/cancel buttons (Enter/Esc) | done | tests/test_editor_ops.py::test_crop_tool_drag_and_confirm |
| ToolCutOut | editor/shapes.py, editor/manager.py, editor/render.py | horizontal/vertical band removal, None/ZigZag/TornEdge/Wave edges, shapes collapse | done | tests/test_editor_ops.py::test_cut_out_edge_effects |
