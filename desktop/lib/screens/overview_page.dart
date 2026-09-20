import 'package:flutter/material.dart';

import '../agent_api.dart';
import 'model_wizard_page.dart';

class OverviewPage extends StatefulWidget {
  const OverviewPage({super.key, required this.api});

  final AgentApi api;

  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage> {
  bool _connected = false;
  bool _accepting = false;
  bool _loading = true;
  String? _error;
  List<ModelSummary> _models = const [];

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final connected = await widget.api.sessionOk();
      final models = connected ? await widget.api.listModels() : <ModelSummary>[];
      if (!mounted) return;
      setState(() {
        _connected = connected;
        _models = models;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _connected = false;
        _loading = false;
        _error = '$error';
      });
    }
  }

  Future<void> _toggleAccepting() async {
    try {
      final accepting = await widget.api.setAccepting(!_accepting);
      if (mounted) setState(() => _accepting = accepting);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('ModelCourier 工作台')),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            _StatusCard(connected: _connected, accepting: _accepting),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const SizedBox(height: 24),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('模型', style: Theme.of(context).textTheme.headlineSmall),
                FilledButton.icon(
                  onPressed: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => ModelWizardPage(api: widget.api)),
                    );
                    _refresh();
                  },
                  icon: const Icon(Icons.add),
                  label: const Text('添加模型'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else if (_models.isEmpty)
              const Card(child: ListTile(title: Text('还没有接入模型'), subtitle: Text('添加 HTTP 服务或 Python 环境开始使用。')))
            else
              ..._models.map(
                (model) => Card(
                  child: ListTile(
                    leading: Icon(model.enabled ? Icons.check_circle : Icons.pause_circle),
                    title: Text(model.displayName),
                    subtitle: Text(model.serviceId),
                    trailing: Text(model.enabled ? '已启用' : '未启用'),
                  ),
                ),
              ),
          ],
        ),
      ),
      floatingActionButton: _connected
          ? FloatingActionButton.extended(
              onPressed: _toggleAccepting,
              icon: Icon(_accepting ? Icons.pause : Icons.play_arrow),
              label: Text(_accepting ? '暂停接单' : '开始接单'),
            )
          : null,
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.connected, required this.accepting});

  final bool connected;
  final bool accepting;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Wrap(
          spacing: 32,
          runSpacing: 12,
          children: [
            _Status(label: '本地 Agent', value: connected ? '已连接' : '未连接'),
            _Status(label: '接单状态', value: accepting ? '接单中' : '已暂停'),
          ],
        ),
      ),
    );
  }
}

class _Status extends StatelessWidget {
  const _Status({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: Theme.of(context).textTheme.labelLarge),
      Text(value, style: Theme.of(context).textTheme.titleLarge),
    ]);
  }
}
