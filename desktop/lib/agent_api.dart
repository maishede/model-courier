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

  Future<AgentStatus> status() async {
    final response = await _client.get(baseUri.resolve('/v1/status'), headers: _headers);
    _check(response);
    return AgentStatus.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<ModelSummary> saveModel(Map<String, dynamic> binding) async {
    final response = await _client.post(
      baseUri.resolve('/v1/models'),
      headers: _headers,
      body: jsonEncode(binding),
    );
    _check(response);
    return ModelSummary.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> verifyModel(
    String bindingId,
    List<int> bytes,
    String mime,
  ) async {
    final request = http.Request(
      'POST',
      baseUri.resolve('/v1/models/$bindingId/verify'),
    )
      ..headers['Authorization'] = 'Bearer $token'
      ..headers['Content-Type'] = mime
      ..bodyBytes = bytes;
    final response = await http.Response.fromStream(await _client.send(request));
    _check(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
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

class AgentStatus {
  const AgentStatus({required this.accepting, required this.runtime});

  final bool accepting;
  final String runtime;

  factory AgentStatus.fromJson(Map<String, dynamic> json) {
    return AgentStatus(
      accepting: json['accepting'] as bool? ?? false,
      runtime: json['runtime'] as String? ?? 'unknown',
    );
  }
}

class ModelSummary {
  const ModelSummary({
    required this.bindingId,
    required this.displayName,
    required this.serviceId,
    required this.enabled,
    required this.verified,
  });

  final String bindingId;
  final String displayName;
  final String serviceId;
  final bool enabled;
  final bool verified;

  factory ModelSummary.fromJson(Map<String, dynamic> json) {
    return ModelSummary(
      bindingId: json['binding_id'] as String,
      displayName: json['display_name'] as String,
      serviceId: json['service_id'] as String,
      enabled: json['enabled'] as bool? ?? false,
      verified: json['verified'] as bool? ?? false,
    );
  }
}
