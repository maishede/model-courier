import 'package:flutter/material.dart';
import 'package:file_selector/file_selector.dart';

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
  String _runtime = 'unknown';
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
      final status = connected ? await widget.api.status() : null;
      final models = connected ? await widget.api.listModels() : <ModelSummary>[];
      if (!mounted) return;
      setState(() {
        _connected = connected;
        _accepting = status?.accepting ?? false;
        _runtime = status?.runtime ?? 'unknown';
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

  Future<void> _verifyModel(ModelSummary model) async {
    const groups = [
      XTypeGroup(
        label: '音频或图片样本',
        extensions: ['wav', 'mp3', 'm4a', 'jpg', 'jpeg', 'png'],
      ),
    ];
    final file = await openFile(acceptedTypeGroups: groups);
    if (file == null || !mounted) return;
    try {
      final result = await widget.api.verifyModel(
        model.bindingId,
        await file.readAsBytes(),
        _mimeFor(file.name),
      );
      if (!mounted) return;
      final succeeded = result['status'] == 'succeeded';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            succeeded ? '真实推理验证通过' : '验证失败：${result['error'] ?? '请检查模型配置'}',
          ),
        ),
      );
      await _refresh();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('验证失败：$error')));
      }
    }
  }

  String _mimeFor(String name) {
    final extension = name.split('.').last.toLowerCase();
    if (extension == 'wav') return 'audio/wav';
    if (extension == 'mp3') return 'audio/mpeg';
    if (extension == 'm4a') return 'audio/mp4';
    if (extension == 'png') return 'image/png';
    return 'image/jpeg';
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
            _StatusCard(
              connected: _connected,
              accepting: _accepting,
              runtime: _runtime,
            ),
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
                    subtitle: Text(
                      '${model.serviceId} · ${model.verified ? '已验证' : '待验证'}',
                    ),
                    trailing: Wrap(
                      spacing: 4,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text(model.enabled ? '已启用' : '未启用'),
                        TextButton(
                          onPressed: () => _verifyModel(model),
                          child: const Text('验证'),
                        ),
                      ],
                    ),
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
  const _StatusCard({
    required this.connected,
    required this.accepting,
    required this.runtime,
  });

  final bool connected;
  final bool accepting;
  final String runtime;

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
            _Status(label: 'Worker', value: runtime),
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
