vm_name = File.basename(Dir.getwd)

Vagrant.configure("2") do |config|
  config.vm.box = "bento/ubuntu-24.04"

  #config.vm.network "forwarded_port", guest: 3000, host: 3000, auto_correct: true
  config.vm.synced_folder ".", "/agent-workspace", type: "virtualbox"
  config.vm.synced_folder "/home/soren/cabinet/Me/Software/Active/easytrans/easytrans-fakerecorder/", "/media/recorder", type: "virtualbox", SharedFoldersEnableSymlinksCreate: false

  config.vm.provider "virtualbox" do |vb|
    vb.memory = "4096"
    vb.cpus = 2
    vb.gui = false
    vb.name = vm_name
    vb.customize ["modifyvm", :id, "--audio", "none"]
    vb.customize ["modifyvm", :id, "--usb", "off"]
  end

  config.vm.provision "shell", inline: <<-SHELL
    export DEBIAN_FRONTEND=noninteractive

    apt-get update
    apt-get install -y git npm
    npm install -g @anthropic-ai/claude-code --no-audit

    chown -R vagrant:vagrant /agent-workspace
    chown -R vagrant:vagrant /media/recorder
  SHELL
  #usermod -aG docker vagrant
end
