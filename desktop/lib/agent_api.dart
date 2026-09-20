import 'dart:convert';

import 'package:http/http.dart' as http;

class AgentApi {
  AgentApi({required this.baseUri, required this.token, http.Client? client})
      : _client = client ?? http.Client();

  final Uri baseUri;
  final String token;
  final http.Client _client;

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      };

  Future<bool> sessionOk() async {
    final response = await _client.get(baseUri.resolve('/v1/session'), headers: _headers);
    return response.statusCode == 200;
  }

  Future<List<ModelSummary>> listModels() async {
    final response = await _client.get(baseUri.resolve('/v1/models'), headers: _headers);
    _check(response);
    final decoded = jsonDecode(response.body) as List<dynamic>;
    return decoded
        .map((item) => ModelSummary.fromJson(item as Map<String, dynamic>))
        .toList();
  }

  Future<bool> setAccepting(bool enabled) async {
    final response = await _client.post(
      baseUri.resolve('/v1/accepting'),
      headers: _headers,
      body: jsonEncode({'enabled': enabled}),
    );
    _check(response);
    return (jsonDecode(response.body) as Map<String, dynamic>)['accepting'] as bool;
  }

  void _check(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw StateError('Agent request failed (${response.statusCode})');
    }
  }
}

class ModelSummary {
  const ModelSummary({
    required this.bindingId,
    required this.displayName,
    required this.serviceId,
    required this.enabled,
  });

  final String bindingId;
  final String displayName;
  final String serviceId;
  final bool enabled;

  factory ModelSummary.fromJson(Map<String, dynamic> json) {
    return ModelSummary(
      bindingId: json['binding_id'] as String,
      displayName: json['display_name'] as String,
      serviceId: json['service_id'] as String,
      enabled: json['enabled'] as bool? ?? false,
    );
  }
}
