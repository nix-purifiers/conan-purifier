final: prev: {
  conan-purifier = {
    readLock = import ./read-lock.nix { lib = final; };
  };
}
