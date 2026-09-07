# -*- mode: ruby -*-
# End-to-end test of playbooks/configure.yml on a real Ubuntu VM (VirtualBox).
# Docker-based Molecule tests cover the roles; this additionally exercises
# systemd services (docker, qemu-guest-agent) and a full cloud image.
#
#   make vagrant-up        # ~10 minutes on first run
#   vagrant ssh            # then: sudo -iu dev claude
#   make vagrant-destroy
#
# Vagrant generates its own inventory, so inventory/group_vars is not loaded;
# role variables are passed directly. No real credentials are needed. Set
# GITHUB_USERNAME (and optionally GITHUB_TOKEN, ANTHROPIC_API_KEY) to use yours.
# Secrets are read from the environment inside the play (lookup), never placed
# on the ansible-playbook command line.
SSH_PUBLIC_KEY = ENV.fetch("SSH_PUBLIC_KEY") do
  path = File.expand_path("~/.ssh/id_ed25519.pub")
  # Vagrant injects its own key; this one only needs to be well-formed.
  File.exist?(path) ? File.read(path).strip : "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderKeyForVagrantTestsOnly0000000 vagrant@test"
end

Vagrant.configure("2") do |config|
  config.vm.box = "bento/ubuntu-24.04"
  config.vm.hostname = "claude-dev"
  config.vm.provider "virtualbox" do |vb|
    vb.memory = 4096
    vb.cpus = 2
  end

  config.vm.provision "ansible" do |ansible|
    ansible.compatibility_mode = "2.0"
    ansible.playbook_command = File.join(__dir__, ".venv", "bin", "ansible-playbook")
    ansible.playbook = "playbooks/configure.yml"
    ansible.groups = { "claude_vms" => ["default"] }
    ansible.extra_vars = {
      configure_wait_for_cloud_init: false,
      common_manage_hostname: false,
      common_user_ssh_public_keys: [SSH_PUBLIC_KEY],
      github_projects_username: ENV.fetch("GITHUB_USERNAME", "octocat"),
      github_projects_token: "{{ lookup('ansible.builtin.env', 'GITHUB_TOKEN') }}",
      github_projects_include: ENV.key?("GITHUB_USERNAME") ? [] : ["Hello-World"],
      claude_code_anthropic_api_key: "{{ lookup('ansible.builtin.env', 'ANTHROPIC_API_KEY') }}",
    }
  end
end
