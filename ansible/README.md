# Ansible

Terraform provisions the Hetzner infrastructure. Ansible configures the Linux
operating system after the machine has been provisioned. Terraform does not run
Ansible, and Ansible does not manage cloud resources.

`playbooks/node.yml` configures a Kubernetes node. It applies `roles/base`,
which owns the common Debian host configuration. The playbook is idempotent and
can be run again safely.

This public repository does not contain production inventory or credentials.
The private deployment repository will provide the production target and its
credentials. `inventory/ci.yml` contains a non-routable placeholder used only
for static validation. Do not use it to run the playbook.

From the repository root, install and run the local static checks with:

```sh
python -m pip install -r ansible/requirements.txt
cd ansible
ansible-lint
ansible-playbook --inventory inventory/ci.yml --syntax-check playbooks/node.yml
```
