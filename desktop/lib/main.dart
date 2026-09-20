import 'package:flutter/material.dart';

import 'agent_api.dart';
import 'screens/overview_page.dart';

void main() {
  const agentUrl = String.fromEnvironment(
    'MODEL_COURIER_AGENT_URL',
    defaultValue: 'http://127.0.0.1:8765',
  );
  const agentToken = String.fromEnvironment('MODEL_COURIER_AGENT_TOKEN');
  runApp(ModelCourierApp(
    api: AgentApi(baseUri: Uri.parse(agentUrl), token: agentToken),
  ));
}

class ModelCourierApp extends StatelessWidget {
  const ModelCourierApp({super.key, required this.api});

  final AgentApi api;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'ModelCourier',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff2563eb)),
        useMaterial3: true,
      ),
      home: OverviewPage(api: api),
    );
  }
}
