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
          ),
          _ChoiceCard(
            icon: Icons.code,
            title: '使用 Conda / Python 环境',
            description: '选择已有解释器和模型适配器，由 Agent 独立进程调用。',
          ),
          _ChoiceCard(
            icon: Icons.play_circle_outline,
            title: '启动并管理本地服务',
            description: '由 Agent 启动服务、等待健康检查并在退出时回收进程。',
          ),
        ],
      ),
    );
  }
}

class _ChoiceCard extends StatelessWidget {
  const _ChoiceCard({required this.icon, required this.title, required this.description});

  final IconData icon;
  final String title;
  final String description;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        contentPadding: const EdgeInsets.all(16),
        leading: Icon(icon, size: 32),
        title: Text(title),
        subtitle: Padding(padding: const EdgeInsets.only(top: 6), child: Text(description)),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('接入表单将在下一步加载。')),
        ),
      ),
    );
  }
}
