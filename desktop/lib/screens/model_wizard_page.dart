import 'package:flutter/material.dart';

import '../agent_api.dart';

class ModelWizardPage extends StatelessWidget {
  const ModelWizardPage({super.key, required this.api});

  final AgentApi api;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('添加本地模型')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Text('选择接入方式', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text('环境、调用协议和模型能力分开配置；同一个 Conda 环境也可以连接本地 HTTP 服务。'),
          const SizedBox(height: 20),
          _ChoiceCard(
            icon: Icons.cloud_outlined,
            title: '连接已有 HTTP 服务',
            description: '适合已经启动的 FunASR、Ollama 或自定义本地接口。',
            onTap: () => _openForm(context, ModelConnectionKind.http),
          ),
          _ChoiceCard(
            icon: Icons.code,
            title: '使用 Conda / Python 环境',
            description: '选择已有解释器和模型适配器，由 Agent 独立进程调用。',
            onTap: () => _openForm(context, ModelConnectionKind.python),
          ),
          _ChoiceCard(
            icon: Icons.play_circle_outline,
            title: '启动并管理本地服务',
            description: '由 Agent 启动服务、等待健康检查并在退出时回收进程。',
            onTap: () => ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('托管服务将在进程管理功能完成后开放。')),
            ),
          ),
        ],
      ),
    );
  }

  void _openForm(BuildContext context, ModelConnectionKind kind) {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => ModelFormPage(api: api, kind: kind)),
    );
  }
}

enum ModelConnectionKind { http, python }

class ModelFormPage extends StatefulWidget {
  const ModelFormPage({super.key, required this.api, required this.kind});

  final AgentApi api;
  final ModelConnectionKind kind;

  @override
  State<ModelFormPage> createState() => _ModelFormPageState();
}

class _ModelFormPageState extends State<ModelFormPage> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _serviceId = TextEditingController(text: 'desktop-gpu-1');
  final _model = TextEditingController();
  final _baseUrl = TextEditingController(text: 'http://127.0.0.1:8000');
  final _path = TextEditingController(text: '/transcribe');
  final _responsePath = TextEditingController(text: 'result.text');
  final _executable = TextEditingController();
  final _arguments = TextEditingController();
  bool _saving = false;

  @override
  void dispose() {
    for (final controller in [
      _name,
      _serviceId,
      _model,
      _baseUrl,
      _path,
      _responsePath,
      _executable,
      _arguments,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isHttp = widget.kind == ModelConnectionKind.http;
    return Scaffold(
      appBar: AppBar(title: Text(isHttp ? '配置 HTTP 模型' : '配置 Python 模型')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            TextFormField(
              controller: _name,
              decoration: const InputDecoration(labelText: '显示名称'),
              validator: _required,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _serviceId,
              decoration: const InputDecoration(labelText: '服务 ID'),
              validator: _required,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _model,
              decoration: const InputDecoration(labelText: '模型名称'),
              validator: _required,
            ),
            const SizedBox(height: 12),
            if (isHttp) ...[
              TextFormField(
                controller: _baseUrl,
                decoration: const InputDecoration(labelText: '服务地址'),
                validator: _required,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _path,
                decoration: const InputDecoration(labelText: '推理路径'),
                validator: _required,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _responsePath,
                decoration: const InputDecoration(labelText: '响应文本路径'),
                validator: _required,
              ),
            ] else ...[
              TextFormField(
                controller: _executable,
                decoration: const InputDecoration(labelText: 'Python 可执行文件路径'),
                validator: _required,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _arguments,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: 'Runner 参数（每行一个）',
                  hintText: '例如：\nC:\\models\\funasr_runner.py',
                ),
              ),
            ],
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _saving ? null : _save,
              icon: _saving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.save),
              label: Text(_saving ? '保存中' : '保存模型'),
            ),
          ],
        ),
      ),
    );
  }

  String? _required(String? value) {
    return value == null || value.trim().isEmpty ? '请填写此字段' : null;
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _saving = true);
    final isHttp = widget.kind == ModelConnectionKind.http;
    final bindingId = _serviceId.text.trim().toLowerCase().replaceAll(
      RegExp(r'[^a-z0-9._-]+'),
      '-',
    );
    final config = isHttp
        ? {
            'base_url': _baseUrl.text.trim(),
            'path': _path.text.trim(),
            'response_path': _responsePath.text.trim(),
          }
        : {
            'executable': _executable.text.trim(),
            'arguments': _arguments.text
                .split('\n')
                .map((argument) => argument.trim())
                .where((argument) => argument.isNotEmpty)
                .toList(),
          };
    final binding = {
      'binding_id': bindingId,
      'display_name': _name.text.trim(),
      'service_id': _serviceId.text.trim(),
      'execution': {
        'profile_id': isHttp ? 'http-$bindingId' : 'python-$bindingId',
        'kind': isHttp ? 'http' : 'python',
        'adapter_id': isHttp ? 'http.transcribe.v1' : 'python.bridge.v1',
        'config': config,
      },
      'model_name': _model.text.trim(),
      'task_type': 'audio.transcribe.v1',
      'enabled': true,
    };
    try {
      await widget.api.saveModel(binding);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('模型配置已保存，请先完成本地验证。')),
      );
      Navigator.of(context).pop();
    } catch (error) {
      if (mounted) {
        setState(() => _saving = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存失败：$error')));
      }
    }
  }
}

class _ChoiceCard extends StatelessWidget {
  const _ChoiceCard({
    required this.icon,
    required this.title,
    required this.description,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String description;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        contentPadding: const EdgeInsets.all(16),
        leading: Icon(icon, size: 32),
        title: Text(title),
        subtitle: Padding(padding: const EdgeInsets.only(top: 6), child: Text(description)),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}
