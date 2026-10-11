| .sxcu import | uploaders/custom/sxcu.py | Newtonsoft-compatible reader + CheckBackwardCompatibility (pre-v13 upgrade) | done | tests/test_sxcu.py::test_import_imgur_like |
| .sxcu export | uploaders/custom/sxcu.py | ShouldSerialize/DefaultValueHandling.Ignore rules, ShareX key order | done | tests/test_sxcu.py::test_round_trip_export_then_import_is_equal |
| RequestMethod | uploaders/custom/uploader.py | GET/POST/PUT/PATCH/DELETE | done | tests/test_custom_uploader.py::test_request_methods_are_used |
| RequestURL | uploaders/custom/uploader.py | syntax with URL-encoded {input}/{filename}, FixPrefix | done | tests/test_custom_uploader.py::test_binary_put_streams_the_raw_file_with_its_mime_type |
| Parameters | uploaders/custom/uploader.py | name parser + syntax, HttpUtility query string | done | tests/test_custom_uploader.py::test_shortener_get_with_parameters_and_no_body |
| Headers | uploaders/custom/uploader.py | name parser + syntax, override defaults | done | tests/test_custom_uploader.py::test_multipart_image_upload_request_shape_and_response_parsing |
| Body None | uploaders/custom/uploader.py | no request content | done | tests/test_custom_uploader.py::test_shortener_get_with_parameters_and_no_body |
| Body MultipartFormData | uploaders/custom/uploader.py | streamed multipart (BodyStream) | done | tests/test_custom_uploader.py::test_multipart_image_upload_request_shape_and_response_parsing |
| Body FormURLEncoded | uploaders/custom/uploader.py | CreateQueryString body | done | tests/test_custom_uploader.py::test_form_urlencoded_body |
| Body JSON | uploaders/custom/uploader.py | Data with JSON-escaped {input}/{filename} | done | tests/test_custom_uploader.py::test_json_body_substitutes_and_escapes_input |
| Body XML | uploaders/custom/uploader.py | Data with XML-escaped {input}/{filename} | done | tests/test_custom_uploader.py::test_xml_body_substitutes_and_escapes_input |
| Body Binary | uploaders/custom/uploader.py | raw streamed file, MIME type from file name | done | tests/test_custom_uploader.py::test_binary_put_streams_the_raw_file_with_its_mime_type |
| Arguments | uploaders/custom/uploader.py | multipart / form fields, name parser + syntax | done | tests/test_custom_uploader.py::test_text_multipart_without_file_form_name_sends_arguments_only |
| FileFormName | uploaders/custom/uploader.py | multipart file field | done | tests/test_custom_uploader.py::test_multipart_quotes_in_names_are_escaped |
| Data | uploaders/custom/uploader.py | JSON/XML body template | done | tests/test_custom_uploader.py::test_json_body_substitutes_and_escapes_input |
| URL / ThumbnailURL / DeletionURL / ErrorMessage | uploaders/custom/uploader.py | ParseResponse; ErrorMessage on non-2xx | done | tests/test_custom_uploader.py::test_http_4xx_parses_error_message_first |
| {response} | uploaders/custom/parser.py | ResponseInfo.ResponseText | done | tests/test_custom_syntax.py::test_response_and_responseurl |
| {responseurl} | uploaders/custom/parser.py | final URL after redirects | done | tests/test_custom_uploader.py::test_redirect_responseurl_and_header |
| {header:} | uploaders/custom/parser.py | case-insensitive response header | done | tests/test_custom_syntax.py::test_header_lookup_is_case_insensitive_and_missing_is_empty |
| {json:} / {json:input&#124;path} (JsonPath) | uploaders/custom/jsonpath.py | port of Newtonsoft JPath / SelectToken | done | tests/test_custom_syntax.py::test_json_paths |
| {xml:} (XPath) | uploaders/custom/parser.py | lxml XPath 1.0, SelectSingleNode value | done | tests/test_custom_syntax.py::test_xml_xpath_on_response |
| {regex:pattern&#124;group} | uploaders/custom/dotnet_regex.py | .NET pattern translation + .NET group numbering | done | tests/test_custom_syntax.py::test_regex_whole_match_lookarounds_and_groups |
| {input} | uploaders/custom/parser.py | text / URL being uploaded | done | tests/test_custom_syntax.py::test_input_and_filename_plain_and_url_encoded |
| {filename} | uploaders/custom/parser.py | file name | done | tests/test_custom_syntax.py::test_input_and_filename_plain_and_url_encoded |
| {random:} | uploaders/custom/parser.py | uniform pick | done | tests/test_custom_syntax.py::test_random_picks_within_choices |
| {select:} (GTK dialog) | uploaders/custom/parser.py | ctx.prompt_select (dialog itself: UI slice) | done | tests/test_custom_syntax.py::test_select_asks_through_ctx_with_non_empty_options |
| {inputbox:} (GTK dialog) | uploaders/custom/parser.py | ctx.prompt_input (dialog itself: UI slice) | done | tests/test_custom_syntax.py::test_inputbox_title_default_and_alias |
| {outputbox:} | uploaders/custom/parser.py | ctx.show_output(title, text) | done | tests/test_custom_syntax.py::test_outputbox_argument_order_is_title_then_text |
| {base64:} | uploaders/custom/parser.py | UTF-8 Base64 | done | tests/test_custom_syntax.py::test_base64 |
| Backslash escaping | uploaders/custom/parser.py | backslash before braces, pipe and backslash | done | tests/test_custom_syntax.py::test_backslash_escapes_braces_pipe_and_backslash |
