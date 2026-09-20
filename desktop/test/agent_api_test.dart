import 'package:flutter_test/flutter_test.dart';

import '../lib/agent_api.dart';

void main() {
  test('parses Agent status and model verification state', () {
    final status = AgentStatus.fromJson({'accepting': true, 'runtime': 'running'});
    final model = ModelSummary.fromJson({
      'binding_id': 'local-http',
      'display_name': 'Local HTTP',
      'service_id': 'desktop-gpu-1',
      'enabled': true,
      'verified': true,
    });

    expect(status.accepting, isTrue);
    expect(status.runtime, 'running');
    expect(model.verified, isTrue);
  });
}
